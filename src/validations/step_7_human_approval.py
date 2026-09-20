"""Step 7: validate an exact, time-bounded human approval artifact."""

from __future__ import annotations

from datetime import datetime, timezone

from src.agents.contracts import ApprovedJiraAction, ProposedJiraAction, Verdict


class HumanApprovalValidator:
    """Enforce the immutable boundary between a proposal and Jira mutation."""

    def validate(
        self,
        approval: ApprovedJiraAction,
        proposal: ProposedJiraAction,
        critic_verdict: Verdict,
        now: datetime | None = None,
    ) -> None:
        """Reject any approval that is stale, mismatched, or not Critic-approved.

        Args:
            approval: Human-supplied approval document.
            proposal: Immutable proposal loaded from PostgreSQL.
            critic_verdict: Stored Critic outcome for the proposal.
            now: Optional timezone-aware clock value for deterministic tests.

        Raises:
            PermissionError: If any required safety condition is not satisfied.
        """
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if critic_verdict is not Verdict.PASS:
            raise PermissionError("Jira write requires a Critic PASS verdict")
        if approval.approved_at > current:
            raise PermissionError("approval time is in the future")
        if approval.expires_at <= current:
            raise PermissionError("approval has expired")
        if approval.expires_at <= approval.approved_at:
            raise PermissionError("approval expiry must follow approval time")
        if approval.issue_key != proposal.issue_key:
            raise PermissionError("approval issue key does not match the proposal")
        if approval.action is not proposal.action:
            raise PermissionError("approval action does not match the proposal")
        if approval.payload != proposal.payload:
            raise PermissionError("approval payload does not match the proposal")
        if approval.payload_hash != proposal.payload_hash:
            raise PermissionError("approval payload hash does not match the proposal")
