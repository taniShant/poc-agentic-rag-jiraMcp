"""Flask application for the local Jira review and approval portal."""

from __future__ import annotations

import hmac
import os
import secrets
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from src.agents.contracts import ApprovedJiraAction, JiraMutationResult
from src.agents.step_8_writer import execute_approved_action
from src.common.config import LocalConfig, load_config
from src.memory.postgres import LocalStorage
from src.ui_portal.repository import PortalRepository, PortalTicket

ApprovalExecutor = Callable[[LocalConfig, ApprovedJiraAction], JiraMutationResult]


def _runtime_config(path: str | Path) -> LocalConfig:
    """Load local.json and apply container-only service address overrides."""
    config = load_config(path)
    postgres_host = os.getenv("APP_POSTGRES_HOST")
    opensearch_url = os.getenv("APP_OPENSEARCH_URL")
    if postgres_host:
        config = replace(
            config,
            postgres=replace(config.postgres, host=postgres_host),
        )
    if opensearch_url:
        config = replace(
            config,
            opensearch=replace(config.opensearch, url=opensearch_url),
        )
    return config


def create_app(
    config_path: str | Path | None = None,
    repository: PortalRepository | None = None,
    approval_executor: ApprovalExecutor = execute_approved_action,
) -> Flask:
    """Create the authenticated administrator portal application.

    Args:
        config_path: Optional local.json path override.
        repository: Optional ticket repository for tests or alternate storage.
        approval_executor: Guarded Jira write function.

    Returns:
        Configured Flask application.
    """
    path = config_path or os.getenv("PORTAL_CONFIG", "ci-cd/env/local.json")
    local_config = _runtime_config(path)
    app = Flask(__name__)
    app.secret_key = local_config.portal.secret_key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    ticket_repository = repository or PortalRepository(
        local_config.opensearch,
        local_config.postgres,
        timeout_seconds=local_config.agent.request_timeout_seconds,
    )

    def csrf_token() -> str:
        """Return a stable random CSRF token for the current browser session."""
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return str(session["csrf_token"])

    def require_csrf() -> None:
        """Reject state-changing requests without the current session token."""
        supplied = request.form.get("csrf_token", "")
        expected = str(session.get("csrf_token", ""))
        if not expected or not hmac.compare_digest(supplied, expected):
            abort(400, "Invalid CSRF token")

    def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
        """Redirect anonymous requests to the administrator login page."""

        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            """Run the protected view only for an authenticated session."""
            if "admin_username" not in session:
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        """Return a container health response without querying dependencies."""
        return {"status": "ok"}, 200

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str | Any:
        """Authenticate the configured local administrator."""
        if request.method == "POST":
            require_csrf()
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            username_ok = hmac.compare_digest(
                username,
                local_config.portal.admin_username,
            )
            password_ok = hmac.compare_digest(
                password,
                local_config.portal.admin_password,
            )
            if username_ok and password_ok:
                session.clear()
                session["admin_username"] = username
                csrf_token()
                return redirect(url_for("tickets"))
            flash("The administrator username or password is incorrect.", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        """End the current administrator session."""
        require_csrf()
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def home() -> Any:
        """Redirect the portal root to the ticket queue."""
        return redirect(url_for("tickets"))

    @app.get("/tickets")
    @login_required
    def tickets() -> str:
        """Render the searchable, status-filtered Jira ticket table."""
        query = request.args.get("q", "").strip()
        selected_status = request.args.get("status", "all").strip().lower()
        all_ticket_rows = ticket_repository.list_tickets(query, "all")
        counts = {
            group: sum(ticket.status_group == group for ticket in all_ticket_rows)
            for group in ("new", "open", "closed")
        }
        counts["all"] = len(all_ticket_rows)
        ticket_rows = (
            [
                ticket
                for ticket in all_ticket_rows
                if ticket.status_group == selected_status
            ]
            if selected_status in {"new", "open", "closed"}
            else all_ticket_rows
        )
        return render_template(
            "tickets.html",
            tickets=ticket_rows,
            counts=counts,
            query=query,
            selected_status=selected_status,
            admin_username=session["admin_username"],
            writes_enabled=(
                local_config.rovo_mcp.enabled and local_config.validation.enabled
            ),
        )

    @app.get("/tickets/<path:issue_key>")
    @login_required
    def ticket_detail(issue_key: str) -> str:
        """Render the problem, proposed resolution, and approval state."""
        ticket = ticket_repository.get_ticket(issue_key)
        if ticket is None:
            abort(404)
        return render_template(
            "ticket_detail.html",
            ticket=ticket,
            admin_username=session["admin_username"],
            writes_enabled=(
                local_config.rovo_mcp.enabled and local_config.validation.enabled
            ),
        )

    @app.post("/tickets/<path:issue_key>/approve")
    @login_required
    def approve_ticket(issue_key: str) -> Any:
        """Execute the exact stored proposal after an administrator click."""
        require_csrf()
        ticket: PortalTicket | None = ticket_repository.get_ticket(issue_key)
        if ticket is None or ticket.approval is None:
            abort(404)
        proposal_view = ticket.approval
        if not proposal_view.approval_needed:
            abort(409, "This Jira proposal no longer needs approval")
        request_id = request.form.get("request_id", "")
        if not hmac.compare_digest(request_id, proposal_view.request_id):
            abort(409, "The displayed proposal has changed; reload the page")
        stored = LocalStorage(local_config.postgres).get_jira_proposal(request_id)
        now = datetime.now(timezone.utc)
        approval = ApprovedJiraAction(
            approval_id=f"portal-{uuid.uuid4()}",
            request_id=request_id,
            issue_key=stored.proposal.issue_key,
            action=stored.proposal.action,
            payload=stored.proposal.payload,
            payload_hash=stored.proposal.payload_hash,
            approved_by=str(session["admin_username"]),
            approved_at=now,
            expires_at=now
            + timedelta(minutes=local_config.portal.approval_ttl_minutes),
        )
        try:
            result = approval_executor(local_config, approval)
            flash(
                f"Approved and executed {result.tool_name} for {issue_key}.",
                "success",
            )
        except Exception:
            app.logger.exception("Jira approval execution failed")
            flash(
                "Approval failed. Review the execution audit or portal logs.",
                "error",
            )
        return redirect(url_for("ticket_detail", issue_key=issue_key))

    return app


def main() -> None:
    """Run the local development server using the configured portal port."""
    path = os.getenv("PORTAL_CONFIG", "ci-cd/env/local.json")
    config = _runtime_config(path)
    create_app(path).run(host="0.0.0.0", port=config.portal.port, debug=False)


if __name__ == "__main__":
    main()
