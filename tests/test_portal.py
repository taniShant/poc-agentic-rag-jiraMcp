"""Tests for the authenticated Jira administrator portal."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.agents.contracts import (
    JiraAction,
    JiraMutationResult,
    ProposedJiraAction,
    StoredJiraProposal,
    Verdict,
)
from src.ui_portal.app import create_app
from src.ui_portal.repository import (
    ApprovalSummary,
    PortalTicket,
    status_group,
    team_name,
)


def _ticket() -> PortalTicket:
    """Build one representative ticket requiring approval."""
    return PortalTicket(
        issue_key="SCRUM-7",
        title="Password reset remains blocked",
        problem="The customer cannot complete the MFA password reset.",
        resolution="Clear the expired MFA registration.",
        status="Open",
        status_group="new",
        team_name="Identity & Access",
        priority="High",
        assignee="IAM Team",
        category="Identity / Password reset",
        updated="2026-09-20T09:00:00Z",
        jira_url="https://example.atlassian.net/browse/SCRUM-7",
        labels=("password-reset",),
        approval=ApprovalSummary(
            request_id="request-7",
            action="add_comment",
            payload={"issueKey": "SCRUM-7", "commentBody": "Verified resolution"},
            payload_hash="a" * 64,
            rationale="Publish the evidence-backed resolution.",
            critic_verdict="PASS",
            agent_response="The MFA registration should be cleared and recreated.",
            execution_status=None,
            approved_by=None,
            approved_at=None,
            error_text=None,
        ),
    )


class FakePortalRepository:
    """Provide deterministic tickets without external services."""

    def list_tickets(
        self,
        query: str = "",
        status_filter: str = "all",
    ) -> list[PortalTicket]:
        """Return the fixture when it matches the requested filters."""
        ticket = _ticket()
        if query and query.lower() not in ticket.title.lower():
            return []
        if status_filter != "all" and status_filter != ticket.status_group:
            return []
        return [ticket]

    def get_ticket(self, issue_key: str) -> PortalTicket | None:
        """Return the fixture for its exact issue key."""
        return _ticket() if issue_key == "SCRUM-7" else None


class PortalTests(unittest.TestCase):
    """Verify login, queue, detail, and guarded approval behavior."""

    def setUp(self) -> None:
        """Create an isolated Flask test client."""
        self.executed = []

        def executor(config: object, approval: object) -> JiraMutationResult:
            """Capture one approved write without contacting Rovo MCP."""
            del config
            self.executed.append(approval)
            return JiraMutationResult(
                approval_id=approval.approval_id,
                issue_key=approval.issue_key,
                action=approval.action,
                tool_name="addOrEditJiraIssueComment",
                response={"status": "success"},
            )

        self.app = create_app(
            "ci-cd/env/local.example.json",
            repository=FakePortalRepository(),  # type: ignore[arg-type]
            approval_executor=executor,  # type: ignore[arg-type]
        )
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def _login(self) -> None:
        """Authenticate with the example configuration credentials."""
        self.client.get("/login")
        with self.client.session_transaction() as browser_session:
            token = browser_session["csrf_token"]
        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "username": "admin",
                "password": "replace-with-a-strong-local-password",
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_anonymous_user_is_redirected_to_login(self) -> None:
        """Ticket data must not be visible before authentication."""
        response = self.client.get("/tickets")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_ticket_queue_and_detail_render_for_admin(self) -> None:
        """The two requested portal screens must show ticket information."""
        self._login()
        queue = self.client.get("/tickets")
        self.assertIn(b"SCRUM-7", queue.data)
        self.assertIn(b"Identity &amp; Access", queue.data)
        detail = self.client.get("/tickets/SCRUM-7")
        self.assertIn(b"Problem statement", detail.data)
        self.assertIn(b"Resolution provided", detail.data)
        self.assertIn(b"Verified resolution", detail.data)

    def test_approval_uses_server_side_stored_proposal(self) -> None:
        """An approval click must construct payload from PostgreSQL, not form data."""
        self._login()
        proposal = ProposedJiraAction(
            issue_key="SCRUM-7",
            action=JiraAction.ADD_COMMENT,
            payload={"issueKey": "SCRUM-7", "commentBody": "Verified resolution"},
            rationale="Publish the response.",
        )

        class FakeStorage:
            """Return the immutable proposal used by the approval route."""

            def __init__(self, config: object) -> None:
                """Accept the production constructor signature."""
                del config

            def get_jira_proposal(self, request_id: str) -> StoredJiraProposal:
                """Return the matching passed proposal."""
                return StoredJiraProposal(
                    request_id=request_id,
                    session_id="portal-test",
                    proposal=proposal,
                    critic_verdict=Verdict.PASS,
                )

        with self.client.session_transaction() as browser_session:
            token = browser_session["csrf_token"]
        with patch("src.ui_portal.app.LocalStorage", FakeStorage):
            response = self.client.post(
                "/tickets/SCRUM-7/approve",
                data={"csrf_token": token, "request_id": "request-7"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(self.executed[0].payload, proposal.payload)

    def test_status_and_team_normalization(self) -> None:
        """Ticket rows must expose stable statuses and readable team names."""
        self.assertEqual(status_group("Done"), "closed")
        self.assertEqual(status_group("In Progress"), "open")
        self.assertEqual(
            team_name({"category": "Access / Application permission"}),
            "Identity & Access",
        )


if __name__ == "__main__":
    unittest.main()
