"""Step 3: create an evidence-first Jira investigation plan."""

from __future__ import annotations

from strands import Agent

from src.agents.contracts import Plan
from src.common.json_utils import parse_json_object


class PlannerAgent:
    """Decompose a support request without executing tools or changing systems."""

    SYSTEM_PROMPT = """
You are the Planner in a Jira support investigation workflow. Decompose the
request into a small evidence-first plan. Decide whether historical support
knowledge, live Jira facts, or both are needed. Do not answer the request and
do not invent issue keys. Return JSON only:
{"objective":"...","steps":["..."],"required_sources":["..."]}
""".strip()

    def __init__(self, model: object) -> None:
        """Create a tool-free Planner using the supplied Strands model."""
        self._agent = Agent(
            model=model,
            system_prompt=self.SYSTEM_PROMPT,
            tools=[],
            callback_handler=None,
        )

    def plan(self, user_request: str) -> Plan:
        """Create a structured investigation plan for one user request."""
        raw = str(self._agent(user_request))
        try:
            value = parse_json_object(raw)
            return Plan(
                objective=str(value.get("objective") or user_request),
                steps=[str(item) for item in value.get("steps", [])],
                required_sources=[
                    str(item) for item in value.get("required_sources", [])
                ],
            )
        except ValueError:
            return Plan(
                objective=user_request,
                steps=[
                    "Search support knowledge for relevant evidence.",
                    "Check live Jira only when current issue facts are required.",
                    "Draft a cited answer and identify unresolved questions.",
                ],
                required_sources=["OpenSearch support knowledge", "Jira when enabled"],
            )
