"""Coordinator for the Planner, Executor, and Critic Strands agents."""

from __future__ import annotations

from typing import Callable

from src.agents.contracts import Critique, LoopRecord, Verdict, WorkflowResult
from src.agents.step_3_planner import PlannerAgent
from src.agents.step_4_executor import ExecutorAgent
from src.agents.step_5_critic import CriticAgent
from src.common.config import ValidationConfig
from src.validations.step_6_critic_reflections import ReflectionLoop


class MultiAgentOrchestrator:
    """Run plan-and-execute followed by optional independent reflection."""

    def __init__(
        self,
        planner: PlannerAgent,
        executor: ExecutorAgent,
        critic: CriticAgent,
        validation: ValidationConfig,
        audit_sink: Callable[[LoopRecord], None] | None = None,
    ) -> None:
        """Store role agents, validation policy, and an optional audit sink."""
        self._planner = planner
        self._executor = executor
        self._critic = critic
        self._validation = validation
        self._audit_sink = audit_sink

    def run(self, user_request: str) -> WorkflowResult:
        """Run all enabled roles and return the final draft and audit trail."""
        plan = self._planner.plan(user_request)
        draft = self._executor.execute(user_request, plan)
        if not self._validation.enabled:
            critique = Critique(
                verdict=Verdict.SKIPPED,
                composite_score=0.0,
                criterion_scores={},
                issues=[],
                preserve=[],
            )
            return WorkflowResult(plan, draft, critique, [])

        final_draft, critique, records = ReflectionLoop(
            self._executor,
            self._critic,
            self._validation,
            self._audit_sink,
        ).run(user_request, plan, draft)
        return WorkflowResult(plan, final_draft, critique, records)
