"""Step 6: coordinate Critic feedback, Executor revision, and escalation."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Protocol

from src.agents.contracts import Critique, Draft, LoopRecord, Plan, Verdict
from src.common.config import ValidationConfig


class ExecutorProtocol(Protocol):
    """Structural interface required from a revising Executor."""

    def execute(
        self,
        user_request: str,
        plan: Plan,
        prior_draft: Draft | None = None,
        critique: Critique | None = None,
    ) -> Draft:
        """Create or revise a response draft."""
        ...


class CriticProtocol(Protocol):
    """Structural interface required from an independent Critic."""

    def evaluate(self, user_request: str, plan: Plan, draft: Draft) -> Critique:
        """Evaluate one response draft."""
        ...


AuditSink = Callable[[LoopRecord], None]


class ReflectionLoop:
    """Apply hard caps, score floors, plateau checks, and hash checks."""

    def __init__(
        self,
        executor: ExecutorProtocol,
        critic: CriticProtocol,
        validation: ValidationConfig,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Initialize the reflection policy and optional durable audit sink."""
        self._executor = executor
        self._critic = critic
        self._validation = validation
        self._audit_sink = audit_sink

    def _record(
        self,
        iteration: int,
        draft: Draft,
        critique: Critique,
        records: list[LoopRecord],
    ) -> None:
        """Append one cycle to memory and the configured durable sink."""
        record = LoopRecord(
            iteration=iteration,
            draft_hash=draft.fingerprint,
            composite_score=critique.composite_score,
            criterion_scores=critique.criterion_scores,
            verdict=critique.verdict,
            issues=critique.issues,
        )
        records.append(record)
        if self._audit_sink is not None:
            self._audit_sink(record)

    def run(
        self,
        user_request: str,
        plan: Plan,
        initial_draft: Draft,
    ) -> tuple[Draft, Critique, list[LoopRecord]]:
        """Refine until pass, escalation, plateau, or the hard cap is reached."""
        candidate = initial_draft
        records: list[LoopRecord] = []
        best_score = float("-inf")
        stagnant_cycles = 0

        for iteration in range(1, self._validation.max_refinements + 2):
            try:
                critique = self._critic.evaluate(user_request, plan, candidate)
            except Exception as error:
                critique = Critique(
                    verdict=Verdict.ESCALATE,
                    composite_score=0.0,
                    criterion_scores={},
                    issues=[f"Critic unavailable: {type(error).__name__}: {error}"],
                    preserve=[],
                )
                self._record(iteration, candidate, critique, records)
                return candidate, critique, records

            if critique.verdict is Verdict.PASS:
                self._record(iteration, candidate, critique, records)
                return candidate, critique, records

            if critique.composite_score > (
                best_score + self._validation.improvement_epsilon
            ):
                best_score = critique.composite_score
                stagnant_cycles = 0
            else:
                stagnant_cycles += 1

            if stagnant_cycles >= self._validation.plateau_patience:
                critique = replace(
                    critique,
                    verdict=Verdict.ESCALATE_PLATEAU,
                    issues=critique.issues + [
                        "Critic score did not improve enough across consecutive cycles."
                    ],
                )
                self._record(iteration, candidate, critique, records)
                return candidate, critique, records

            if iteration > self._validation.max_refinements:
                critique = replace(
                    critique,
                    verdict=Verdict.ESCALATE,
                    issues=critique.issues + ["Maximum refinement count reached."],
                )
                self._record(iteration, candidate, critique, records)
                return candidate, critique, records

            self._record(iteration, candidate, critique, records)
            revised = self._executor.execute(
                user_request,
                plan,
                prior_draft=candidate,
                critique=critique,
            )
            if revised.fingerprint == candidate.fingerprint:
                critique = replace(
                    critique,
                    verdict=Verdict.ESCALATE,
                    issues=critique.issues + [
                        "Executor returned an identical draft; further loops stopped."
                    ],
                )
                self._record(iteration + 1, revised, critique, records)
                return revised, critique, records
            candidate = revised

        raise RuntimeError("reflection loop exhausted without a terminal verdict")
