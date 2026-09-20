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

    def render(self) -> str:
        """Render the answer with an explicit human-review flag if escalated."""
        if self.critique.verdict in {Verdict.PASS, Verdict.SKIPPED}:
            return self.draft.body
        reason = "; ".join(self.critique.issues) or "quality threshold not met"
        return (
            f"HUMAN REVIEW REQUIRED ({self.critique.verdict.value}): {reason}\n\n"
            f"{self.draft.body}"
        )
