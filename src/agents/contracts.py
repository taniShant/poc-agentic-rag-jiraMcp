"""Typed contracts shared by Planner, Executor, Critic, and orchestrator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """Possible outcomes from the bounded reflection workflow."""

    PASS = "PASS"
    REFINE = "REFINE"
    ESCALATE = "ESCALATE"
    ESCALATE_PLATEAU = "ESCALATE_PLATEAU"
    SKIPPED = "SKIPPED"


class JiraAction(str, Enum):
    """Supported human-approved Jira mutation types."""

    CREATE_ISSUE = "create_issue"
    EDIT_ISSUE = "edit_issue"
    ADD_COMMENT = "add_comment"
    TRANSITION_ISSUE = "transition_issue"


@dataclass(frozen=True)
class ProposedJiraAction:
    """Exact Jira mutation proposed by the Executor for human approval."""

    issue_key: str
    action: JiraAction
    payload: dict[str, Any]
    rationale: str

    @property
    def payload_hash(self) -> str:
        """Return a stable hash over the target, action, and exact payload."""
        value = {
            "issue_key": self.issue_key,
            "action": self.action.value,
            "payload": self.payload,
        }
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Return the proposal as a JSON-serializable mapping."""
        return {
            "issue_key": self.issue_key,
            "action": self.action.value,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProposedJiraAction":
        """Parse and validate a proposed Jira action from model JSON."""
        issue_key = str(value.get("issue_key", "")).strip()
        rationale = str(value.get("rationale", "")).strip()
        payload = value.get("payload")
        if not issue_key:
            raise ValueError("proposed_action.issue_key must not be empty")
        if not isinstance(payload, dict) or not payload:
            raise ValueError("proposed_action.payload must be a non-empty object")
        return cls(
            issue_key=issue_key,
            action=JiraAction(str(value.get("action", ""))),
            payload=payload,
            rationale=rationale,
        )


@dataclass(frozen=True)
class ApprovedJiraAction:
    """Human approval authorizing one exact stored Jira proposal."""

    approval_id: str
    request_id: str
    issue_key: str
    action: JiraAction
    payload: dict[str, Any]
    payload_hash: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApprovedJiraAction":
        """Parse an approval JSON document and normalize its timestamps."""
        payload = value.get("payload")
        if not isinstance(payload, dict) or not payload:
            raise ValueError("approval payload must be a non-empty object")

        def parse_time(name: str) -> datetime:
            """Parse one required timezone-aware ISO-8601 timestamp."""
            raw = str(value.get(name, "")).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                raise ValueError(f"{name} must include a timezone")
            return parsed.astimezone(timezone.utc)

        result = cls(
            approval_id=str(value.get("approval_id", "")).strip(),
            request_id=str(value.get("request_id", "")).strip(),
            issue_key=str(value.get("issue_key", "")).strip(),
            action=JiraAction(str(value.get("action", ""))),
            payload=payload,
            payload_hash=str(value.get("payload_hash", "")).strip(),
            approved_by=str(value.get("approved_by", "")).strip(),
            approved_at=parse_time("approved_at"),
            expires_at=parse_time("expires_at"),
        )
        required = {
            "approval_id": result.approval_id,
            "request_id": result.request_id,
            "issue_key": result.issue_key,
            "payload_hash": result.payload_hash,
            "approved_by": result.approved_by,
        }
        missing = [name for name, item in required.items() if not item]
        if missing:
            raise ValueError(f"approval fields must not be empty: {', '.join(missing)}")
        return result


@dataclass(frozen=True)
class JiraMutationResult:
    """Auditable result returned by the deterministic Writer role."""

    approval_id: str
    issue_key: str
    action: JiraAction
    tool_name: str
    response: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mutation result."""
        value = asdict(self)
        value["action"] = self.action.value
        return value


@dataclass(frozen=True)
class StoredJiraProposal:
    """Jira proposal and quality verdict restored from PostgreSQL."""

    request_id: str
    session_id: str
    proposal: ProposedJiraAction
    critic_verdict: Verdict


@dataclass(frozen=True)
class Plan:
    """Planner output describing the objective and evidence-gathering steps."""

    objective: str
    steps: list[str]
    required_sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the plan."""
        return asdict(self)


@dataclass(frozen=True)
class Draft:
    """Executor output containing a response and its supporting evidence."""

    body: str
    citations: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    proposed_action: ProposedJiraAction | None = None

    @property
    def fingerprint(self) -> str:
        """Return a stable hash used to detect identical revisions."""
        canonical = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Critique:
    """Structured Critic assessment of one Executor draft."""

    verdict: Verdict
    composite_score: float
    criterion_scores: dict[str, int]
    issues: list[str]
    preserve: list[str]

    def refinement_context(self) -> str:
        """Serialize actionable feedback for the Executor revision prompt."""
        return json.dumps(
            {
                "verdict": self.verdict.value,
                "composite_score": self.composite_score,
                "criterion_scores": self.criterion_scores,
                "issues_to_fix": self.issues,
                "preserve": self.preserve,
            },
            indent=2,
        )


@dataclass(frozen=True)
class LoopRecord:
    """Immutable audit record for one critique cycle."""

    iteration: int
    draft_hash: str
    composite_score: float
    criterion_scores: dict[str, int]
    verdict: Verdict
    issues: list[str]
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the cycle."""
        value = asdict(self)
        value["verdict"] = self.verdict.value
        return value


@dataclass(frozen=True)
class WorkflowResult:
    """Final multi-agent result plus its complete reflection history."""

    plan: Plan
    draft: Draft
    critique: Critique
    audit_trail: list[LoopRecord]

    def render(self, request_id: str | None = None) -> str:
        """Render the answer with an explicit human-review flag if escalated."""
        if self.critique.verdict in {Verdict.PASS, Verdict.SKIPPED}:
            answer = self.draft.body
            if (
                self.critique.verdict is Verdict.PASS
                and self.draft.proposed_action is not None
            ):
                proposal = self.draft.proposed_action.to_dict()
                approval = {
                    "approval_id": "replace-with-unique-approval-id",
                    "request_id": request_id or "replace-with-request-id",
                    "issue_key": proposal["issue_key"],
                    "action": proposal["action"],
                    "payload": proposal["payload"],
                    "payload_hash": proposal["payload_hash"],
                    "approved_by": "replace-with-approver-identity",
                    "approved_at": "replace-with-ISO-8601-time",
                    "expires_at": "replace-with-ISO-8601-time",
                }
                answer += (
                    "\n\nJIRA WRITE PROPOSED — HUMAN APPROVAL REQUIRED\n"
                    + json.dumps(approval, indent=2, ensure_ascii=False)
                )
            return answer
        reason = "; ".join(self.critique.issues) or "quality threshold not met"
        return (
            f"HUMAN REVIEW REQUIRED ({self.critique.verdict.value}): {reason}\n\n"
            f"{self.draft.body}"
        )
