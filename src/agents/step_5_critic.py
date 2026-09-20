"""Step 5: independently evaluate the Executor's structured response."""

from __future__ import annotations

import json

from strands import Agent

from src.agents.contracts import Critique, Draft, Plan, Verdict
from src.common.config import ValidationConfig
from src.common.json_utils import parse_json_object

CRITERIA = ("accuracy", "groundedness", "completeness", "relevance")


class CriticAgent:
    """Independently score Executor drafts without access to runtime tools."""

    SYSTEM_PROMPT = """
You are the independent Critic in a Jira support workflow. Judge the draft only
against the request, plan, citations, and evidence supplied. Score accuracy,
groundedness, completeness, and relevance from 1 to 5. Flag unsupported ticket
keys, status values, URLs, commands, and resolution claims. Give specific fixes
and list correct content that revisions must preserve. If proposed_action is
present, verify it is supported by the evidence, contains exact tool arguments,
targets the intended issue, and uses only create_issue, edit_issue, add_comment,
or transition_issue. Reject delete or generic write actions. Return JSON only:
{"criterion_scores":{"accuracy":1,"groundedness":1,"completeness":1,
"relevance":1},"issues":["specific fix"],"preserve":["correct section"]}
""".strip()

    def __init__(self, model: object, validation: ValidationConfig) -> None:
        """Create a tool-free Critic and store its acceptance thresholds."""
        self._validation = validation
        self._agent = Agent(
            model=model,
            system_prompt=self.SYSTEM_PROMPT,
            tools=[],
            callback_handler=None,
        )

    def evaluate(self, user_request: str, plan: Plan, draft: Draft) -> Critique:
        """Score one draft and return a normalized structured critique."""
        payload = {
            "user_request": user_request,
            "plan": plan.to_dict(),
            "candidate": {
                "body": draft.body,
                "citations": draft.citations,
                "evidence": draft.evidence,
                "unresolved_questions": draft.unresolved_questions,
                "proposed_action": (
                    draft.proposed_action.to_dict()
                    if draft.proposed_action is not None
                    else None
                ),
            },
        }
        value = parse_json_object(
            str(self._agent(json.dumps(payload, ensure_ascii=False)))
        )
        raw_scores = value.get("criterion_scores", {})
        if not isinstance(raw_scores, dict):
            raw_scores = {}
        scores = {
            criterion: min(max(int(raw_scores.get(criterion, 1)), 1), 5)
            for criterion in CRITERIA
        }
        composite = round(sum(scores.values()) / len(CRITERIA), 3)
        passed = (
            composite >= self._validation.pass_score
            and min(scores.values()) >= self._validation.min_individual_score
        )
        return Critique(
            verdict=Verdict.PASS if passed else Verdict.REFINE,
            composite_score=composite,
            criterion_scores=scores,
            issues=[str(item) for item in value.get("issues", [])],
            preserve=[str(item) for item in value.get("preserve", [])],
        )
