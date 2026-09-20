"""Unit tests for configuration, hybrid ranking, and agent reflection."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from src.agents.contracts import (
    ApprovedJiraAction,
    Critique,
    Draft,
    JiraAction,
    Plan,
    ProposedJiraAction,
    Verdict,
)
from src.agents.step_8_writer import WriterAgent
from src.common.config import load_config
from src.common.config import ValidationConfig
from src.mcp.rovo_mcp_oauth import RovoFileTokenStorage
from src.validations.step_6_critic_reflections import ReflectionLoop
from src.validations.step_7_human_approval import HumanApprovalValidator
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
        self.assertEqual(
            config.rovo_mcp.server_url,
            "https://mcp.atlassian.com/v2/mcp",
        )
        self.assertNotIn("executeWrite", config.rovo_mcp.tools_for_role("reader"))
        self.assertNotIn("executeDestructive", config.rovo_mcp.tools_for_role("writer"))
        self.assertIn("createJiraIssue", config.rovo_mcp.tools_for_role("writer"))

    def test_rovo_configuration_rejects_write_execution_tools(self) -> None:
        """Rovo configuration must never expose generic write pathways."""
        document = json.loads(
            Path("ci-cd/env/local.example.json").read_text(encoding="utf-8")
        )
        document["rovoMcp"]["enabled"] = True
        document["rovoMcp"]["roles"]["writer"]["allowed_tools"].append(
            "executeWrite"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "generic/destructive"):
                load_config(path)

    def test_human_approval_requires_exact_unexpired_passed_proposal(self) -> None:
        """Only an exact approval for a Critic-passed proposal may proceed."""
        now = datetime.now(timezone.utc)
        proposal = ProposedJiraAction(
            "SCRUM-7",
            JiraAction.ADD_COMMENT,
            {"issueKey": "SCRUM-7", "commentBody": "Approved response"},
            "Share the verified resolution.",
        )
        approval = ApprovedJiraAction(
            "approval-7",
            "request-7",
            proposal.issue_key,
            proposal.action,
            proposal.payload,
            proposal.payload_hash,
            "support.manager@example.com",
            now - timedelta(minutes=1),
            now + timedelta(minutes=10),
        )
        HumanApprovalValidator().validate(approval, proposal, Verdict.PASS, now)
        changed = ApprovedJiraAction(
            approval.approval_id,
            approval.request_id,
            approval.issue_key,
            approval.action,
            {"issueKey": "SCRUM-7", "commentBody": "Changed"},
            approval.payload_hash,
            approval.approved_by,
            approval.approved_at,
            approval.expires_at,
        )
        with self.assertRaisesRegex(PermissionError, "payload"):
            HumanApprovalValidator().validate(changed, proposal, Verdict.PASS, now)
        with self.assertRaisesRegex(PermissionError, "Critic PASS"):
            HumanApprovalValidator().validate(approval, proposal, Verdict.REFINE, now)

    def test_writer_calls_only_mapped_tool_with_unchanged_payload(self) -> None:
        """The Writer must pass approved arguments directly to one safe tool."""

        class FakeMcpClient:
            """Capture one deterministic MCP tool invocation."""

            def __init__(self) -> None:
                """Initialize without a recorded call."""
                self.call = None

            def call_tool_sync(
                self,
                tool_use_id: str,
                name: str,
                arguments: dict[str, object],
            ) -> dict[str, object]:
                """Capture the call and return a successful response."""
                self.call = (tool_use_id, name, arguments)
                return {"status": "success", "content": [{"text": "ok"}]}

        now = datetime.now(timezone.utc)
        payload = {"issueKey": "SCRUM-9", "commentBody": "Exact text"}
        approval = ApprovedJiraAction(
            "approval-9",
            "request-9",
            "SCRUM-9",
            JiraAction.ADD_COMMENT,
            payload,
            ProposedJiraAction(
                "SCRUM-9", JiraAction.ADD_COMMENT, payload, "Reason"
            ).payload_hash,
            "manager@example.com",
            now,
            now + timedelta(minutes=5),
        )
        client = FakeMcpClient()
        result = WriterAgent(
            client,  # type: ignore[arg-type]
            ("addOrEditJiraIssueComment",),
        ).execute(approval)
        self.assertEqual(client.call[1:], ("addOrEditJiraIssueComment", payload))
        self.assertEqual(result.tool_name, "addOrEditJiraIssueComment")

    def test_rovo_token_storage_round_trip(self) -> None:
        """OAuth tokens and DCR metadata must survive secure local storage."""
        async def exercise() -> None:
            """Write and reload representative OAuth state."""
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "tokens.json"
                storage = RovoFileTokenStorage(path)
                tokens = OAuthToken(
                    access_token="access-value",
                    refresh_token="refresh-value",
                    expires_in=3600,
                )
                client_info = OAuthClientInformationFull(
                    redirect_uris=["http://127.0.0.1:8765/oauth/callback"],
                    client_id="dynamic-client",
                    token_endpoint_auth_method="none",
                )
                await storage.set_tokens(tokens)
                await storage.set_client_info(client_info)
                self.assertEqual((await storage.get_tokens()).access_token, "access-value")
                self.assertEqual(
                    (await storage.get_client_info()).client_id,
                    "dynamic-client",
                )
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        import asyncio

        asyncio.run(exercise())

    def test_postgres_migrations_are_kept_under_scripts(self) -> None:
        """Bootstrap's PostgreSQL migrations must live in scripts/postgresDb."""
        migration_directory = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "postgresDb"
        )
        self.assertTrue(
            (migration_directory / "001_memory_and_idempotency.sql").is_file()
        )
        self.assertTrue(
            (migration_directory / "002_agent_reflection_audit.sql").is_file()
        )
        self.assertTrue(
            (migration_directory / "003_jira_action_approval.sql").is_file()
        )

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

    def test_reflection_loop_refines_until_response_passes(self) -> None:
        """A failed Critic verdict must trigger an Executor revision."""

        class FakeExecutor:
            """Return one deterministic improved draft."""

            def execute(
                self,
                user_request: str,
                plan: Plan,
                prior_draft: Draft | None = None,
                critique: Critique | None = None,
            ) -> Draft:
                """Append evidence to the prior draft."""
                del user_request, plan, critique
                assert prior_draft is not None
                return Draft(body=prior_draft.body + " with evidence")

        class FakeCritic:
            """Return one failure followed by one passing review."""

            def __init__(self) -> None:
                """Initialize an empty review history."""
                self.calls = 0

            def evaluate(
                self, user_request: str, plan: Plan, draft: Draft
            ) -> Critique:
                """Return a deterministic review based on invocation count."""
                del user_request, plan, draft
                self.calls += 1
                return Critique(
                    verdict=Verdict.PASS if self.calls > 1 else Verdict.REFINE,
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
        records = []
        draft, critique, audit = ReflectionLoop(
            FakeExecutor(),
            critic,
            ValidationConfig(True, 2, 4.0, 3),
            records.append,
        ).run(
            "question",
            Plan("answer", ["search"], ["OpenSearch"]),
            Draft("draft"),
        )
        self.assertEqual(draft.body, "draft with evidence")
        self.assertEqual(critique.verdict, Verdict.PASS)
        self.assertEqual(critic.calls, 2)
        self.assertEqual(len(audit), 2)
        self.assertEqual(records, audit)

    def test_identical_revision_escalates_immediately(self) -> None:
        """An unchanged Executor draft must stop the loop immediately."""

        class IdenticalExecutor:
            """Return the exact prior draft."""

            def execute(
                self,
                user_request: str,
                plan: Plan,
                prior_draft: Draft | None = None,
                critique: Critique | None = None,
            ) -> Draft:
                """Return the supplied prior draft unchanged."""
                del user_request, plan, critique
                assert prior_draft is not None
                return prior_draft

        class RejectingCritic:
            """Always request a revision."""

            def evaluate(
                self, user_request: str, plan: Plan, draft: Draft
            ) -> Critique:
                """Return one deterministic failing critique."""
                del user_request, plan, draft
                return Critique(
                    Verdict.REFINE,
                    2.0,
                    {criterion: 2 for criterion in (
                        "accuracy", "groundedness", "completeness", "relevance"
                    )},
                    ["Add evidence."],
                    ["Keep the summary."],
                )

        _, critique, audit = ReflectionLoop(
            IdenticalExecutor(),
            RejectingCritic(),
            ValidationConfig(True, 8, 4.0, 3),
        ).run(
            "question",
            Plan("answer", ["search"], ["OpenSearch"]),
            Draft("unchanged"),
        )
        self.assertEqual(critique.verdict, Verdict.ESCALATE)
        self.assertIn("identical", critique.issues[-1].lower())
        self.assertEqual(len(audit), 2)

    def test_score_plateau_escalates_before_hard_cap(self) -> None:
        """Repeated non-improving scores must trigger plateau escalation."""

        class ChangingExecutor:
            """Produce distinct drafts so only score plateau ends the loop."""

            def __init__(self) -> None:
                """Initialize the revision counter."""
                self.calls = 0

            def execute(
                self,
                user_request: str,
                plan: Plan,
                prior_draft: Draft | None = None,
                critique: Critique | None = None,
            ) -> Draft:
                """Append a counter to ensure each draft hash changes."""
                del user_request, plan, critique
                assert prior_draft is not None
                self.calls += 1
                return Draft(f"{prior_draft.body}-{self.calls}")

        class FlatCritic:
            """Return the same failing score on every cycle."""

            def evaluate(
                self, user_request: str, plan: Plan, draft: Draft
            ) -> Critique:
                """Return a flat score and actionable issue."""
                del user_request, plan, draft
                return Critique(
                    Verdict.REFINE,
                    2.0,
                    {criterion: 2 for criterion in (
                        "accuracy", "groundedness", "completeness", "relevance"
                    )},
                    ["Still incomplete."],
                    [],
                )

        _, critique, audit = ReflectionLoop(
            ChangingExecutor(),
            FlatCritic(),
            ValidationConfig(True, 8, 4.0, 3, plateau_patience=2),
        ).run(
            "question",
            Plan("answer", ["search"], ["OpenSearch"]),
            Draft("draft"),
        )
        self.assertEqual(critique.verdict, Verdict.ESCALATE_PLATEAU)
        self.assertEqual(len(audit), 3)


if __name__ == "__main__":
    unittest.main()
