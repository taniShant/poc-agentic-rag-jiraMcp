"""Ollama-backed response critique and bounded refinement loop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

import httpx

from src.common.config import OllamaConfig, ValidationConfig

CRITERIA = ("accuracy", "groundedness", "completeness", "relevance")


@dataclass(frozen=True)
class Critique:
    """Structured result from one independent response review."""

    passed: bool
    composite_score: float
    criterion_scores: dict[str, int]
    issues: list[str]
    preserve: list[str]

    def refinement_context(self) -> str:
        """Serialize actionable review feedback for the executor agent."""
        return json.dumps(
            {
                "composite_score": self.composite_score,
                "criterion_scores": self.criterion_scores,
                "issues_to_fix": self.issues,
                "preserve": self.preserve,
            },
            indent=2,
        )


class ResponseCritic:
    """Review an agent response with a separate deterministic Ollama call."""

    SYSTEM_PROMPT = """
You are an independent reviewer for a Jira investigation assistant.
Score accuracy, groundedness, completeness, and relevance from 1 to 5.
Do not assume a claim is true merely because it is confidently phrased.
Flag unsupported ticket keys, statuses, URLs, commands, and resolution claims.
Return JSON only with this schema:
{
  "criterion_scores": {
    "accuracy": 1,
    "groundedness": 1,
    "completeness": 1,
    "relevance": 1
  },
  "issues": ["specific required correction"],
  "preserve": ["specific correct part"]
}
""".strip()

    def __init__(
        self,
        ollama: OllamaConfig,
        validation: ValidationConfig,
        timeout_seconds: int,
    ) -> None:
        """Initialize the review client and thresholds."""
        self._ollama = ollama
        self._validation = validation
        self._client = httpx.Client(
            base_url=ollama.base_url.rstrip("/"), timeout=timeout_seconds
        )

    def evaluate(self, user_request: str, response_text: str) -> Critique:
        """Score a response and return normalized actionable feedback."""
        response = self._client.post(
            "/api/chat",
            json={
                "model": self._ollama.chat_model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "user_request": user_request,
                                "candidate_response": response_text,
                            }
                        ),
                    },
                ],
            },
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "{}")
        result = json.loads(content)
        raw_scores = result.get("criterion_scores", {})
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
            passed=passed,
            composite_score=composite,
            criterion_scores=scores,
            issues=[str(item) for item in result.get("issues", [])],
            preserve=[str(item) for item in result.get("preserve", [])],
        )


class CriticLoop:
    """Run independent critique and bounded executor refinement."""

    def __init__(self, critic: ResponseCritic, max_refinements: int) -> None:
        """Initialize the critic and maximum number of revision attempts."""
        self._critic = critic
        self._max_refinements = max_refinements

    def run(
        self,
        user_request: str,
        initial_response: str,
        revise: Callable[[str, Critique], str],
    ) -> str:
        """Return the first passing response or the final bounded revision."""
        candidate = initial_response
        for refinement in range(self._max_refinements + 1):
            critique = self._critic.evaluate(user_request, candidate)
            if critique.passed or refinement == self._max_refinements:
                return candidate
            revised = revise(candidate, critique)
            if revised.strip() == candidate.strip():
                return candidate
            candidate = revised
        return candidate
