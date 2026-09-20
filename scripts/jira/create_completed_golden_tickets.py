"""Create an idempotent set of 100 completed Jira golden-data tickets.

The script is deliberately separate from the agent and its MCP tools. It reads
Jira credentials from ``ci-cd/env/local.json`` and requires explicit write-mode
configuration plus command-line confirmation before changing Jira Cloud.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class JiraSettings:
    """Jira connection and safety settings loaded from ``local.json``."""

    enabled: bool
    base_url: str
    email: str
    api_token: str
    project_key: str
    read_only: bool


@dataclass(frozen=True)
class Scenario:
    """Reusable service-request scenario with an approved resolution."""

    category: str
    summary: str
    request: str
    business_justification: str
    checks: tuple[str, ...]
    resolution_steps: tuple[str, ...]
    validation: tuple[str, ...]
    resolution_code: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class GoldenTicket:
    """One deterministic, fully answered Jira golden-data record."""

    external_id: str
    category: str
    summary: str
    requester: str
    requester_email: str
    department: str
    location: str
    environment: str
    opened_date: str
    completed_date: str
    request: str
    business_justification: str
    checks: tuple[str, ...]
    resolution_steps: tuple[str, ...]
    validation: tuple[str, ...]
    resolution_code: str
    labels: tuple[str, ...]

    def description(self) -> str:
        """Return a structured description suitable for retrieval and evaluation."""
        return "\n".join(
            [
                f"GOLDEN DATASET CASE: {self.external_id}",
                f"Category: {self.category}",
                f"Historical request date: {self.opened_date}",
                f"Historical completion date: {self.completed_date}",
                f"Requester: {self.requester} ({self.requester_email})",
                f"Department: {self.department}",
                f"Location: {self.location}",
                f"Environment: {self.environment}",
                "",
                "REQUEST / SYMPTOM",
                self.request,
                "",
                "BUSINESS JUSTIFICATION",
                self.business_justification,
                "",
                "PRE-CHANGE CHECKS",
                *[f"- {item}" for item in self.checks],
                "",
                "APPROVED RESOLUTION",
                *[
                    f"{position}. {item}"
                    for position, item in enumerate(self.resolution_steps, start=1)
                ],
                "",
                "VALIDATION AND CLOSURE EVIDENCE",
                *[f"- {item}" for item in self.validation],
                "",
                f"Resolution code: {self.resolution_code}",
                "Data classification: synthetic demonstration data",
            ]
        )

    def resolution_comment(self) -> str:
        """Return the canonical closure comment, including an idempotency marker."""
        marker = f"[GOLDEN-DATASET-RESOLUTION {self.external_id}]"
        steps = "\n".join(
            f"{position}. {item}"
            for position, item in enumerate(self.resolution_steps, start=1)
        )
        validation = "\n".join(f"- {item}" for item in self.validation)
        return (
            f"{marker}\n"
            f"Resolution code: {self.resolution_code}\n\n"
            f"Actions completed:\n{steps}\n\n"
            f"Validation:\n{validation}\n\n"
            f"Historical closure date represented by this synthetic case: "
            f"{self.completed_date}."
        )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "Identity / Password reset",
        "Password reset and account unlock completed",
        "The user could not sign in after repeated password attempts and the directory account became locked.",
        "Restore access to approved corporate services without changing the user's existing authorization.",
        ("Verified the requester through the approved identity challenge.", "Confirmed the account was active and not under an HR or security hold.", "Checked sign-in logs for indicators of credential abuse."),
        ("Unlocked the directory account.", "Issued a temporary password through the approved secure channel.", "Required password change at the next sign-in and revoked active sessions."),
        ("User signed in with the temporary credential.", "User changed the password and completed MFA.", "No further failed sign-ins appeared during the observation window."),
        "Solved - identity verified and access restored",
        ("password-reset", "account-unlock", "identity"),
    ),
    Scenario(
        "Identity / New account",
        "New employee account provisioned",
        "A manager requested a standard corporate account for an approved new starter.",
        "Provide day-one access aligned to the employee's role and start date.",
        ("Matched the request to an approved onboarding record.", "Confirmed manager, department, start date, and license availability.", "Verified that no active or dormant duplicate identity existed."),
        ("Created the directory identity using the naming standard.", "Assigned the baseline license and role-based groups.", "Sent activation instructions to the manager through the approved channel."),
        ("Account appeared in the directory and target applications.", "Manager confirmed the expected baseline access.", "User completed first sign-in and MFA registration."),
        "Fulfilled - account provisioned",
        ("new-account", "onboarding", "identity"),
    ),
    Scenario(
        "Access / Application permission",
        "Application role permission granted",
        "The user required an additional application role to perform an assigned business function.",
        "Enable an approved duty while maintaining least privilege and segregation of duties.",
        ("Confirmed manager and application-owner approval.", "Checked role conflicts and segregation-of-duties rules.", "Verified the requested access scope and expiry date."),
        ("Assigned the least-privilege application role.", "Recorded the approval reference in the access register.", "Forced a fresh application session so the entitlement was re-evaluated."),
        ("User demonstrated the required function.", "A prohibited administrative function remained unavailable.", "Entitlement appeared in the access review report."),
        "Fulfilled - approved least-privilege role assigned",
        ("application-access", "least-privilege", "approval"),
    ),
    Scenario(
        "Endpoint / Software installation",
        "Approved software installed on managed endpoint",
        "The user requested an approved software package that was not present on the managed workstation.",
        "Provide the supported tool required for the user's assigned work.",
        ("Confirmed the device was corporate-managed and compliant.", "Verified software approval, license availability, and platform compatibility.", "Checked that no conflicting or unsupported version was installed."),
        ("Deployed the signed package through endpoint management.", "Applied the standard configuration and security policy.", "Recorded the installed version and license allocation."),
        ("Application launched successfully under the user's account.", "Endpoint protection reported no finding.", "User completed the documented functional smoke test."),
        "Fulfilled - supported software installed",
        ("software-install", "endpoint", "license"),
    ),
    Scenario(
        "Access / Active Directory group",
        "Active Directory group membership granted",
        "The user needed membership in a controlled directory group for an approved resource.",
        "Grant narrowly scoped access required by the user's current role.",
        ("Validated group owner and line-manager approval.", "Reviewed nested-group impact and privileged-access classification.", "Confirmed no conflicting entitlement was present."),
        ("Added the user to the approved group.", "Recorded the access owner and review date.", "Triggered directory synchronization and refreshed the user session."),
        ("Effective membership was visible on the target resource.", "User accessed the approved resource only.", "Access-review evidence contained the new membership."),
        "Fulfilled - directory group membership assigned",
        ("active-directory", "group-membership", "access"),
    ),
    Scenario(
        "Change / Production project move",
        "Approved project release promoted to production",
        "A tested project release required controlled promotion from staging to production.",
        "Deliver an approved production change while preserving rollback and auditability.",
        ("Verified change approval, maintenance window, test evidence, and business owner sign-off.", "Confirmed backups, deployment artifact checksum, dependencies, and rollback plan.", "Checked monitoring health and absence of conflicting production changes."),
        ("Placed the service in the approved change window.", "Promoted the immutable release artifact using the deployment runbook.", "Applied configuration, ran migrations, and retained the previous release for rollback."),
        ("Smoke tests and health checks passed.", "Business owner validated the critical user journey.", "Monitoring remained normal and the change record was closed."),
        "Successful - production deployment validated",
        ("production-change", "deployment", "change-management"),
    ),
    Scenario(
        "Network / VPN access",
        "Remote-access VPN enabled",
        "The user required remote access to approved internal services from a managed device.",
        "Support authorized remote work without exposing unrestricted network access.",
        ("Confirmed employment status, manager approval, and device compliance.", "Verified MFA enrollment and the required network access profile.", "Checked for an existing suspended or duplicate VPN entitlement."),
        ("Assigned the restricted VPN profile.", "Installed the managed client configuration.", "Registered the entitlement for periodic access review."),
        ("User connected with MFA from the managed endpoint.", "Only approved network routes were reachable.", "Authentication and tunnel logs showed a successful compliant session."),
        "Fulfilled - restricted VPN access enabled",
        ("vpn", "remote-access", "network"),
    ),
    Scenario(
        "Identity / MFA replacement",
        "MFA method replaced after device change",
        "The user replaced or lost the registered authentication device and could not complete MFA.",
        "Restore secure authentication after verifying the account holder.",
        ("Completed enhanced identity verification.", "Reviewed recent sign-ins and confirmed the old device was no longer trusted.", "Checked that the request was not associated with an active security incident."),
        ("Revoked the old MFA registration and active sessions.", "Issued a time-limited registration method.", "Registered the replacement authenticator and required a new sign-in."),
        ("User completed MFA with the replacement device.", "The old method no longer authenticated.", "The identity audit log recorded revocation and registration."),
        "Solved - MFA securely re-registered",
        ("mfa", "device-replacement", "identity"),
    ),
    Scenario(
        "Data / Database read access",
        "Read-only database access granted",
        "An analyst required read-only access to an approved reporting database and schema.",
        "Enable reporting while preventing data modification and unnecessary data exposure.",
        ("Confirmed data-owner and manager approval.", "Reviewed data classification and mandatory training.", "Validated that the requested schema and duration matched the business need."),
        ("Created or mapped the user to the read-only database role.", "Restricted connectivity to the approved network path.", "Recorded the grant, owner, scope, and expiry in the access register."),
        ("A permitted SELECT query succeeded.", "INSERT, UPDATE, DELETE, and administrative operations were denied.", "Audit logging captured the test session."),
        "Fulfilled - scoped read-only data access granted",
        ("database", "read-only", "data-access"),
    ),
    Scenario(
        "Messaging / Shared mailbox",
        "Shared mailbox access configured",
        "The user required access to a team mailbox for an approved operational responsibility.",
        "Allow the team to manage shared correspondence with accountable permissions.",
        ("Confirmed mailbox owner and manager approval.", "Validated whether read/manage or send-as permission was required.", "Checked for conflicting delegation and retention restrictions."),
        ("Granted only the approved mailbox permissions.", "Applied the standard automapping setting.", "Recorded the delegate and review date."),
        ("Mailbox appeared in the user's client after token refresh.", "The user could perform approved actions only.", "Mailbox audit logging recorded the validation action."),
        "Fulfilled - shared mailbox delegation applied",
        ("shared-mailbox", "messaging", "delegation"),
    ),
    Scenario(
        "Messaging / Distribution list",
        "Distribution list membership updated",
        "The user needed membership in a controlled business distribution list.",
        "Ensure the user receives role-relevant operational communications.",
        ("Confirmed list owner approval and membership criteria.", "Verified that the address was active and not already nested in the list.", "Checked restrictions for confidential or regulated communications."),
        ("Added the user as a direct member of the approved list.", "Triggered directory synchronization.", "Documented ownership and the next review date."),
        ("Membership was visible in the directory.", "A controlled test message was delivered.", "No unintended distribution lists were added."),
        "Fulfilled - distribution membership updated",
        ("distribution-list", "messaging", "membership"),
    ),
    Scenario(
        "Endpoint / Hardware replacement",
        "Faulty managed laptop replaced",
        "The managed laptop had a confirmed hardware fault that prevented reliable work.",
        "Restore productivity while preserving corporate data and asset accountability.",
        ("Confirmed the hardware diagnosis and warranty status.", "Verified backup or approved data-recovery status.", "Matched an available replacement to the user's role profile."),
        ("Prepared the replacement with the standard managed image.", "Restored approved user data and required applications.", "Transferred asset ownership and quarantined the faulty device for repair or secure disposal."),
        ("Device compliance, encryption, endpoint protection, and patching were healthy.", "User completed sign-in and application smoke tests.", "Asset inventory reflected both the replacement and returned device."),
        "Solved - compliant replacement device issued",
        ("hardware", "laptop", "asset-management"),
    ),
    Scenario(
        "License / SaaS allocation",
        "SaaS product license allocated",
        "The user required a licensed SaaS product for an approved role responsibility.",
        "Provide required functionality while controlling recurring license cost.",
        ("Confirmed manager approval and product eligibility.", "Checked available license inventory and duplicate subscriptions.", "Verified that a lower-cost role would not satisfy the requirement."),
        ("Allocated the minimum suitable license tier.", "Assigned the standard security and sharing policy.", "Recorded the cost center and license-review date."),
        ("User accessed the licensed features.", "The license console showed one active assignment.", "No duplicate license remained allocated."),
        "Fulfilled - approved license allocated",
        ("license", "saas", "cost-control"),
    ),
    Scenario(
        "Identity / Service account",
        "Service account provisioned with controlled credentials",
        "An approved workload required a non-interactive identity for a defined integration.",
        "Enable automation with traceable ownership and minimum privileges.",
        ("Confirmed application-owner and security approval.", "Documented purpose, owner, environments, required permissions, and expiry.", "Verified that a managed identity was unavailable or unsuitable."),
        ("Created the non-interactive account using the service naming standard.", "Assigned only the approved roles and denied interactive sign-in where supported.", "Stored the generated credential in the approved secrets manager with rotation metadata."),
        ("The workload authenticated and completed its permitted operation.", "Interactive and unrelated resource access were denied.", "Monitoring, rotation, and ownership records were present."),
        "Fulfilled - controlled service identity provisioned",
        ("service-account", "automation", "secrets"),
    ),
    Scenario(
        "Engineering / Source repository access",
        "Repository permission granted",
        "An engineer required access to a source repository for an assigned project.",
        "Enable contribution while protecting branches and sensitive repositories.",
        ("Confirmed repository owner and manager approval.", "Verified employment status, security training, and team membership.", "Selected the minimum read or write role required."),
        ("Added the user through the approved repository team.", "Applied branch protection and required-review policies.", "Recorded the access grant for periodic review."),
        ("User cloned the repository and created a test branch where authorized.", "Protected-branch direct push remained blocked.", "Repository audit logs recorded the access change."),
        "Fulfilled - repository access granted",
        ("source-control", "repository", "engineering-access"),
    ),
    Scenario(
        "Integration / SFTP partner access",
        "Restricted SFTP exchange access configured",
        "An approved partner exchange required access to a dedicated SFTP location.",
        "Transfer agreed files through a controlled, auditable channel.",
        ("Confirmed data-owner, security, and partner approval.", "Validated data classification, source IPs, retention, and exchange path.", "Verified a unique account and SSH public key were supplied."),
        ("Created a chrooted account restricted to the approved directory.", "Installed the partner public key and source-IP restriction.", "Enabled transfer logging, retention, and expiration controls."),
        ("A test upload and download succeeded in the approved path.", "Parent and peer directories were inaccessible.", "Transfer and authentication events appeared in monitoring."),
        "Fulfilled - restricted file exchange enabled",
        ("sftp", "partner", "file-transfer"),
    ),
    Scenario(
        "Network / Firewall rule",
        "Approved firewall connectivity enabled",
        "An application required a specific source-to-destination network flow that was blocked.",
        "Enable an approved service dependency without creating broad network exposure.",
        ("Confirmed application owner and security approval.", "Validated source, destination, protocol, port, environment, and expiry.", "Reviewed overlap, threat exposure, and an available rollback plan."),
        ("Created the narrow firewall rule in the approved change window.", "Attached logging and the documented expiration date.", "Committed the policy and monitored deployment health."),
        ("The intended TCP connection succeeded.", "Unapproved ports and sources remained blocked.", "Firewall logs showed only the expected test traffic."),
        "Successful - least-privilege network flow enabled",
        ("firewall", "network", "change-management"),
    ),
    Scenario(
        "Identity / Offboarding",
        "Departed user access removed",
        "HR submitted an approved termination or departure event for a user account.",
        "Remove access on time while preserving required business records and legal holds.",
        ("Validated the HR event, effective time, manager, and legal-hold requirements.", "Collected the user's accounts, privileged roles, devices, sessions, and owned resources.", "Confirmed mailbox and file-transfer instructions with the manager."),
        ("Disabled sign-in and revoked active sessions and MFA methods.", "Removed groups, licenses, tokens, VPN, and application access.", "Transferred approved data ownership and updated the asset-return record."),
        ("Authentication attempts were denied after the effective time.", "Access reports showed no active entitlements.", "Manager and HR confirmed data transfer and asset status."),
        "Completed - access revoked and records retained",
        ("offboarding", "access-revocation", "identity"),
    ),
    Scenario(
        "Cloud / Console access",
        "Cloud console role assigned",
        "An engineer required console access to an approved cloud account and environment.",
        "Support operational duties with time-bound, least-privilege cloud authorization.",
        ("Confirmed manager, account owner, and security approval.", "Mapped requested duties to the approved role catalog.", "Checked privileged-role conflicts, MFA, and training status."),
        ("Assigned the approved federated role to the engineer's access group.", "Applied session duration and environment restrictions.", "Recorded owner, purpose, and access-review date."),
        ("Federated sign-in with MFA succeeded.", "A permitted read or deployment check succeeded.", "Administrative actions outside the role remained denied and logged."),
        "Fulfilled - federated cloud role assigned",
        ("cloud", "console-access", "least-privilege"),
    ),
    Scenario(
        "Support / Email delivery",
        "Email delivery issue diagnosed and resolved",
        "Messages from an approved sender were delayed or quarantined before reaching the user.",
        "Restore legitimate mail flow without weakening organization-wide protection.",
        ("Collected message ID, sender, recipient, timestamp, and expected subject.", "Traced the message and reviewed authentication and filtering verdicts.", "Confirmed the sender and content were legitimate with the business owner."),
        ("Corrected the narrowly scoped sender or policy configuration.", "Released the validated message where policy allowed.", "Retained anti-phishing, malware, and bulk-mail protections."),
        ("A new authenticated test message was delivered.", "Message trace showed the expected policy result.", "Unrelated spoofed test mail remained blocked."),
        "Solved - legitimate mail flow restored",
        ("email", "delivery", "messaging"),
    ),
)


def load_jira_settings(config_path: Path) -> JiraSettings:
    """Load the Jira section from the local JSON configuration.

    Args:
        config_path: Path to ``local.json``.

    Returns:
        Validated Jira connection and safety settings.

    Raises:
        ValueError: If the Jira section or a required value is missing.
    """
    document = json.loads(config_path.read_text(encoding="utf-8"))
    jira = document.get("jiraMcp")
    if not isinstance(jira, dict):
        raise ValueError("local.json must contain a jira object")
    required = ("base_url", "email", "api_token", "project_key")
    missing = [name for name in required if not str(jira.get(name, "")).strip()]
    if missing:
        raise ValueError(f"Missing Jira settings: {', '.join(missing)}")
    return JiraSettings(
        enabled=bool(jira.get("enabled")),
        base_url=str(jira["base_url"]).rstrip("/"),
        email=str(jira["email"]),
        api_token=str(jira["api_token"]),
        project_key=str(jira["project_key"]),
        read_only=bool(jira.get("read_only", True)),
    )


def generate_golden_tickets(count: int = 100) -> list[GoldenTicket]:
    """Generate deterministic, synthetic, fully resolved service cases.

    Args:
        count: Number of tickets to generate, from 1 through 100.

    Returns:
        Golden tickets distributed evenly across service categories.

    Raises:
        ValueError: If count is outside the supported range.
    """
    if not 1 <= count <= 100:
        raise ValueError("count must be between 1 and 100")
    departments = ("Finance", "Engineering", "Customer Support", "Sales", "Operations")
    locations = ("London", "Dublin", "Manchester", "Edinburgh", "Remote UK")
    environments = ("Corporate", "Development", "Test", "Staging", "Production")
    start = date(2025, 1, 6)
    tickets: list[GoldenTicket] = []
    for index in range(count):
        scenario = SCENARIOS[index % len(SCENARIOS)]
        variant = index // len(SCENARIOS) + 1
        ticket_number = index + 1
        opened = start + timedelta(days=index * 3)
        completed = opened + timedelta(days=1 + (index % 3))
        department = departments[index % len(departments)]
        location = locations[(index * 2) % len(locations)]
        environment = environments[(index * 3) % len(environments)]
        external_id = f"golden-ticket-{ticket_number:03d}"
        tickets.append(
            GoldenTicket(
                external_id=external_id,
                category=scenario.category,
                summary=f"[Golden {ticket_number:03d}] {scenario.summary} - case {variant}",
                requester=f"Synthetic User {ticket_number:03d}",
                requester_email=f"synthetic.user{ticket_number:03d}@example.invalid",
                department=department,
                location=location,
                environment=environment,
                opened_date=opened.isoformat(),
                completed_date=completed.isoformat(),
                request=scenario.request,
                business_justification=scenario.business_justification,
                checks=scenario.checks,
                resolution_steps=scenario.resolution_steps,
                validation=scenario.validation,
                resolution_code=scenario.resolution_code,
                labels=(
                    "local-agent-golden-data",
                    external_id,
                    *scenario.tags,
                    f"case-variant-{variant}",
                ),
            )
        )
    return tickets


def _adf_document(text: str) -> dict[str, Any]:
    """Convert multiline plain text into an Atlassian Document Format document.

    Args:
        text: Plain text whose line breaks should be preserved.

    Returns:
        ADF document accepted by Jira REST API v3.
    """
    content: list[dict[str, Any]] = []
    for line in text.splitlines():
        paragraph: dict[str, Any] = {"type": "paragraph", "content": []}
        if line:
            paragraph["content"] = [{"type": "text", "text": line}]
        content.append(paragraph)
    return {"type": "doc", "version": 1, "content": content}


def _adf_text(value: Any) -> str:
    """Flatten an ADF value into text for resolution-marker detection.

    Args:
        value: Arbitrarily nested ADF value.

    Returns:
        Concatenated text nodes.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_adf_text(item) for item in value)))
    if isinstance(value, dict):
        own = str(value.get("text") or "")
        children = _adf_text(value.get("content", []))
        return "\n".join(part for part in (own, children) if part)
    return ""


class JiraGoldenDatasetClient:
    """Create, answer, transition, and verify deterministic Jira tickets."""

    def __init__(self, settings: JiraSettings, timeout_seconds: int = 30) -> None:
        """Initialize an authenticated Jira REST API client.

        Args:
            settings: Jira credentials and project configuration.
            timeout_seconds: Per-request network timeout.
        """
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.base_url,
            auth=(settings.email, settings.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a Jira request with bounded retry for throttling and outages.

        Args:
            method: HTTP method.
            path: Jira REST API path.
            **kwargs: Arguments passed to ``httpx.Client.request``.

        Returns:
            Successful HTTP response.

        Raises:
            RuntimeError: If Jira rejects the request after bounded retries.
        """
        response: httpx.Response | None = None
        for attempt in range(5):
            response = self._client.request(method, path, **kwargs)
            if response.status_code not in {429, 502, 503, 504}:
                break
            retry_after = response.headers.get("Retry-After", "")
            delay = min(float(retry_after), 30.0) if retry_after.isdigit() else min(2**attempt, 8)
            time.sleep(delay)
        assert response is not None
        if response.is_error:
            body = response.text.replace(self._settings.api_token, "[REDACTED]")[:1200]
            raise RuntimeError(
                f"Jira {method} {path} failed with {response.status_code}: {body}"
            )
        return response

    def verify_identity_and_project(self) -> None:
        """Verify authentication and access to the configured Jira project."""
        self._request("GET", "/rest/api/3/myself")
        self._request("GET", f"/rest/api/3/project/{self._settings.project_key}")

    def find_ticket(self, external_id: str) -> dict[str, Any] | None:
        """Find the unique existing ticket carrying an external-ID label.

        Args:
            external_id: Synthetic case label.

        Returns:
            Existing Jira issue or ``None``.

        Raises:
            RuntimeError: If duplicate issues already use the label.
        """
        jql = (
            f'project = "{self._settings.project_key}" '
            f'AND labels = "{external_id}" ORDER BY created ASC'
        )
        response = self._request(
            "GET",
            "/rest/api/3/search/jql",
            params={"jql": jql, "maxResults": 2, "fields": "status,resolution,labels"},
        )
        issues = response.json().get("issues", [])
        if len(issues) > 1:
            raise RuntimeError(f"Duplicate Jira issues found for {external_id}")
        return issues[0] if issues else None

    def create_ticket(self, ticket: GoldenTicket, issue_type: str) -> str:
        """Create one Jira issue containing the golden question and answer.

        Args:
            ticket: Golden dataset record.
            issue_type: Jira issue-type name available in the configured project.

        Returns:
            Newly created Jira issue key.
        """
        response = self._request(
            "POST",
            "/rest/api/3/issue",
            json={
                "fields": {
                    "project": {"key": self._settings.project_key},
                    "summary": ticket.summary,
                    "issuetype": {"name": issue_type},
                    "labels": list(ticket.labels),
                    "description": _adf_document(ticket.description()),
                }
            },
        )
        return str(response.json()["key"])

    def ensure_resolution_comment(self, issue_key: str, ticket: GoldenTicket) -> bool:
        """Add the canonical resolution comment when its marker is absent.

        Args:
            issue_key: Jira issue key.
            ticket: Golden dataset record.

        Returns:
            ``True`` when a comment was added, otherwise ``False``.
        """
        marker = f"[GOLDEN-DATASET-RESOLUTION {ticket.external_id}]"
        response = self._request(
            "GET",
            f"/rest/api/3/issue/{issue_key}/comment",
            params={"maxResults": 100, "orderBy": "created"},
        )
        comments = response.json().get("comments", [])
        if any(marker in _adf_text(comment.get("body")) for comment in comments):
            return False
        self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/comment",
            json={"body": _adf_document(ticket.resolution_comment())},
        )
        return True

    def issue_state(self, issue_key: str) -> dict[str, str]:
        """Return normalized workflow and resolution state for one issue.

        Args:
            issue_key: Jira issue key.

        Returns:
            Status name, status category, and resolution name.
        """
        response = self._request(
            "GET",
            f"/rest/api/3/issue/{issue_key}",
            params={"fields": "status,resolution"},
        )
        fields = response.json().get("fields", {})
        status = fields.get("status") or {}
        category = status.get("statusCategory") or {}
        resolution = fields.get("resolution") or {}
        return {
            "status": str(status.get("name") or ""),
            "category": str(category.get("key") or ""),
            "resolution": str(resolution.get("name") or ""),
        }

    def transition_to_done(self, issue_key: str, done_status: str | None) -> bool:
        """Move an issue through available transitions into the Done category.

        Args:
            issue_key: Jira issue key.
            done_status: Optional exact completed-status name to require.

        Returns:
            ``True`` when at least one transition was executed.

        Raises:
            RuntimeError: If no safe path to a Done-category status is available.
        """
        changed = False
        for _ in range(5):
            state = self.issue_state(issue_key)
            if state["category"].lower() == "done":
                if done_status and state["status"].lower() != done_status.lower():
                    raise RuntimeError(
                        f"{issue_key} is already {state['status']!r}, not requested "
                        f"completed status {done_status!r}"
                    )
                return changed
            response = self._request(
                "GET",
                f"/rest/api/3/issue/{issue_key}/transitions",
                params={"expand": "transitions.fields"},
            )
            transitions = response.json().get("transitions", [])
            if done_status:
                done = [
                    item
                    for item in transitions
                    if str((item.get("to") or {}).get("name", "")).lower()
                    == done_status.lower()
                ]
            else:
                done = [
                    item
                    for item in transitions
                    if str(
                        ((item.get("to") or {}).get("statusCategory") or {}).get(
                            "key", ""
                        )
                    ).lower()
                    == "done"
                ]
                preference = {"resolved": 0, "closed": 1, "done": 2}
                done.sort(
                    key=lambda item: preference.get(
                        str((item.get("to") or {}).get("name", "")).lower(), 99
                    )
                )
            candidates = done
            if not candidates:
                candidates = [
                    item
                    for item in transitions
                    if str(
                        ((item.get("to") or {}).get("statusCategory") or {}).get(
                            "key", ""
                        )
                    ).lower()
                    == "indeterminate"
                ]
            if not candidates:
                names = [str(item.get("name")) for item in transitions]
                raise RuntimeError(
                    f"No transition path toward Done for {issue_key}; available: {names}"
                )
            transition = candidates[0]
            self._request(
                "POST",
                f"/rest/api/3/issue/{issue_key}/transitions",
                json={"transition": {"id": str(transition["id"])}},
            )
            changed = True
        raise RuntimeError(f"Transition limit reached before {issue_key} entered Done")

    def seed(
        self,
        tickets: list[GoldenTicket],
        issue_type: str,
        done_status: str | None,
    ) -> dict[str, int]:
        """Create or reconcile every golden ticket and verify completion.

        Args:
            tickets: Deterministic records to seed.
            issue_type: Jira issue type used for new records.
            done_status: Optional exact completed-status name.

        Returns:
            Counters for created, existing, commented, and transitioned issues.
        """
        counters = {"created": 0, "existing": 0, "commented": 0, "transitioned": 0}
        for position, ticket in enumerate(tickets, start=1):
            existing = self.find_ticket(ticket.external_id)
            if existing:
                issue_key = str(existing["key"])
                counters["existing"] += 1
                action = "existing"
            else:
                issue_key = self.create_ticket(ticket, issue_type)
                counters["created"] += 1
                action = "created"
            if self.ensure_resolution_comment(issue_key, ticket):
                counters["commented"] += 1
            if self.transition_to_done(issue_key, done_status):
                counters["transitioned"] += 1
            state = self.issue_state(issue_key)
            if state["category"].lower() != "done":
                raise RuntimeError(f"{issue_key} did not reach the Done category")
            resolution_note = state["resolution"] or "workflow did not set resolution"
            print(
                f"[{position:03d}/{len(tickets):03d}] {action:8s} {issue_key:12s} "
                f"status={state['status']} resolution={resolution_note}"
            )
        return counters


def export_dataset(tickets: list[GoldenTicket], destination: Path) -> None:
    """Write an inspectable JSON representation without Jira credentials.

    Args:
        tickets: Golden records to export.
        destination: Output JSON path.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps([asdict(ticket) for ticket in tickets], indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Parse arguments, preview the dataset, and optionally seed Jira Cloud."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="ci-cd/env/local.json")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--issue-type", default="Task")
    parser.add_argument(
        "--done-status",
        help="Exact completed status, for example Done, Resolved, or Closed",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        help="Write the generated non-secret dataset to a JSON file",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create comments and transition Jira issues",
    )
    parser.add_argument(
        "--confirm-project",
        help="Required with --execute; must exactly match jiraMcp.project_key",
    )
    arguments = parser.parse_args()

    tickets = generate_golden_tickets(arguments.count)
    categories = sorted({ticket.category for ticket in tickets})
    print(f"Prepared {len(tickets)} deterministic tickets across {len(categories)} categories.")
    if arguments.export_json:
        export_dataset(tickets, arguments.export_json)
        print(f"Exported preview to {arguments.export_json}.")
    if not arguments.execute:
        print("Preview only; Jira was not changed. Add --execute and --confirm-project.")
        return

    settings = load_jira_settings(Path(arguments.config).expanduser().resolve())
    if not settings.enabled:
        parser.error("Set jiraMcp.enabled=true in local.json before executing")
    if settings.read_only:
        parser.error("Set jiraMcp.read_only=false temporarily before executing")
    if arguments.confirm_project != settings.project_key:
        parser.error(
            f"--confirm-project must exactly match configured key {settings.project_key!r}"
        )
    if settings.api_token.startswith("replace-"):
        parser.error("Replace the Jira API-token placeholder before executing")

    client = JiraGoldenDatasetClient(settings)
    try:
        client.verify_identity_and_project()
        counters = client.seed(tickets, arguments.issue_type, arguments.done_status)
    finally:
        client.close()
    print(f"Completed Jira golden-data seed: {counters}")
    print("Set jiraMcp.read_only=true again before running the agent.")


if __name__ == "__main__":
    main()
