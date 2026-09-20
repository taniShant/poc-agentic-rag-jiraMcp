"""Unit tests for configuration, hybrid ranking, and critic refinement."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.common.config import load_config
from src.validations.critic_loop import CriticLoop, Critique
from src.vectorDb.ingestion import (
    normalize_confluence_page,
    normalize_golden_ticket,
    normalize_known_issue,
)
from src.vectorDb.retrieval import HybridKnowledgeRetriever


class LocalRuntimeTests(unittest.TestCase):
    """Verify local configuration and hybrid ranking without external services."""

    def test_example_configuration_loads(self) -> None:
        """The committed example must remain compatible with the config schema."""
        config = load_config("ci-cd/env/local.example.json")
        self.assertEqual(config.opensearch.url, "http://localhost:9200")
        self.assertEqual(config.postgres.database, "agentic_local")

    def test_postgres_migration_is_kept_under_scripts(self) -> None:
        """Bootstrap's PostgreSQL migration must live in scripts/postgresDb."""
        migration = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "postgresDb"
            / "001_memory_and_idempotency.sql"
        )
        self.assertTrue(migration.is_file())

    def test_rank_fusion_rewards_results_present_in_both_lists(self) -> None:
        """A ticket present in both rankings should outrank single-list hits."""
        keyword_hits = [
            {"_id": "A", "_source": {"issue_key": "A"}},
            {"_id": "B", "_source": {"issue_key": "B"}},
        ]
        vector_hits = [
            {"_id": "B", "_source": {"issue_key": "B"}},
            {"_id": "C", "_source": {"issue_key": "C"}},
        ]
        results = HybridKnowledgeRetriever._reciprocal_rank_fusion(
            keyword_hits, vector_hits, 0.5, 0.5, 3
        )
        self.assertEqual([result["issue_key"] for result in results], ["B", "A", "C"])

    def test_every_knowledge_source_has_a_stable_document_id(self) -> None:
        """Source normalizers must create namespaced vector document IDs."""
        jira = normalize_golden_ticket(
            {
                "external_id": "golden-1",
                "summary": "Reset password",
                "labels": [],
            }
        )
        confluence = normalize_confluence_page(
            {"page_id": "42", "title": "Password runbook"}
        )
        known_issue = normalize_known_issue(
            {"known_issue_id": "KI-7", "title": "MFA loop"}
        )
        self.assertEqual(jira["document_id"], "jira-sample:golden-1")
        self.assertEqual(confluence["document_id"], "confluence:42")
        self.assertEqual(known_issue["document_id"], "known-issue:KI-7")

    def test_critic_loop_refines_until_response_passes(self) -> None:
        """A failed critique must trigger a bounded response revision."""

        class FakeCritic:
            """Return one failure followed by one passing review."""

            def __init__(self) -> None:
                """Initialize an empty review history."""
                self.calls = 0

            def evaluate(self, user_request: str, response_text: str) -> Critique:
                """Return a deterministic review based on invocation count."""
                self.calls += 1
                return Critique(
                    passed=self.calls > 1,
                    composite_score=5.0 if self.calls > 1 else 2.0,
                    criterion_scores={
                        "accuracy": 5 if self.calls > 1 else 2,
                        "groundedness": 5 if self.calls > 1 else 2,
                        "completeness": 5 if self.calls > 1 else 2,
                        "relevance": 5 if self.calls > 1 else 2,
                    },
                    issues=[] if self.calls > 1 else ["Add evidence."],
                    preserve=[],
                )

        critic = FakeCritic()
        result = CriticLoop(critic, max_refinements=2).run(
            "question",
            "draft",
            lambda current, critique: current + " with evidence",
        )
        self.assertEqual(result, "draft with evidence")
        self.assertEqual(critic.calls, 2)


if __name__ == "__main__":
    unittest.main()
