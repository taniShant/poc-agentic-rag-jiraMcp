"""PostgreSQL storage for local conversational memory and idempotency."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pg8000.dbapi

from src.common.config import PostgresConfig
from src.agents.contracts import (
    ApprovedJiraAction,
    JiraAction,
    JiraMutationResult,
    LoopRecord,
    ProposedJiraAction,
    StoredJiraProposal,
    Verdict,
)


def _split_sql_statements(sql_text: str) -> list[str]:
    """Split simple migration SQL on semicolons outside string literals.

    Args:
        sql_text: SQL migration contents without procedural dollar-quoted blocks.

    Returns:
        Non-empty SQL statements without trailing semicolons.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_string = False
    index = 0
    while index < len(sql_text):
        character = sql_text[index]
        buffer.append(character)
        if character == "'":
            if in_string and index + 1 < len(sql_text) and sql_text[index + 1] == "'":
                buffer.append(sql_text[index + 1])
                index += 1
            else:
                in_string = not in_string
        elif character == ";" and not in_string:
            statement = "".join(buffer[:-1]).strip()
            if statement:
                statements.append(statement)
            buffer = []
        index += 1
    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


class LocalStorage:
    """Persist memory, idempotency results, and reflection audit records."""

    def __init__(self, config: PostgresConfig) -> None:
        """Initialize the PostgreSQL repository.

        Args:
            config: PostgreSQL connection settings loaded from ``local.json``.
        """
        self._config = config

    def _connect(self) -> pg8000.dbapi.Connection:
        """Open a PostgreSQL DB-API connection.

        Returns:
            A new PostgreSQL connection owned by the caller.
        """
        ssl_context: bool | None = True if self._config.ssl else None
        return pg8000.dbapi.connect(
            host=self._config.host,
            port=self._config.port,
            database=self._config.database,
            user=self._config.username,
            password=self._config.password,
            ssl_context=ssl_context,
            timeout=15,
        )

    def migrate(self, migration_directory: Path) -> list[str]:
        """Apply ordered idempotent SQL migration files.

        Args:
            migration_directory: Directory containing ``*.sql`` migration files.

        Returns:
            Names of the migration files executed.
        """
        migrations = sorted(migration_directory.glob("*.sql"))
        connection = self._connect()
        try:
            cursor = connection.cursor()
            for migration in migrations:
                sql_text = migration.read_text(encoding="utf-8")
                for statement in _split_sql_statements(sql_text):
                    cursor.execute(statement)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return [migration.name for migration in migrations]

    def begin_request(self, request_id: str, session_id: str, prompt: str) -> str | None:
        """Claim a request ID or return its previously completed response.

        Args:
            request_id: Caller-provided unique request identifier.
            session_id: Conversation identifier.
            prompt: User prompt protected by the idempotency key.

        Returns:
            A cached response for a completed duplicate, otherwise ``None``.

        Raises:
            ValueError: If a request ID is reused for different input or is active.
        """
        request_hash = hashlib.sha256(
            f"{session_id}\0{prompt}".encode("utf-8")
        ).hexdigest()
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT request_hash, status, response_text
                FROM idempotency_record
                WHERE request_id = %s
                FOR UPDATE
                """,
                (request_id,),
            )
            existing = cursor.fetchone()
            if existing:
                existing_hash, status, response_text = existing
                if existing_hash != request_hash:
                    raise ValueError("request_id was already used for different input")
                if status == "completed":
                    connection.commit()
                    return response_text
                if status == "processing":
                    raise ValueError("request_id is already being processed")
                cursor.execute(
                    """
                    UPDATE idempotency_record
                    SET status = 'processing', error_text = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE request_id = %s
                    """,
                    (request_id,),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO idempotency_record (
                        request_id, session_id, request_hash, status
                    ) VALUES (%s, %s, %s, 'processing')
                    """,
                    (request_id, session_id, request_hash),
                )
            connection.commit()
            return None
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_request(self, request_id: str, response: str) -> None:
        """Store a completed idempotent response.

        Args:
            request_id: Claimed request identifier.
            response: Final assistant response text.
        """
        self._execute(
            """
            UPDATE idempotency_record
            SET status = 'completed', response_text = %s, error_text = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE request_id = %s
            """,
            (response, request_id),
        )

    def fail_request(self, request_id: str, error: str) -> None:
        """Record a failed request so an explicit retry can reclaim it.

        Args:
            request_id: Claimed request identifier.
            error: Sanitized failure description.
        """
        self._execute(
            """
            UPDATE idempotency_record
            SET status = 'failed', error_text = %s, updated_at = CURRENT_TIMESTAMP
            WHERE request_id = %s
            """,
            (error[:4000], request_id),
        )

    def add_memory(self, session_id: str, role: str, content: str) -> None:
        """Append one user or assistant message to durable memory.

        Args:
            session_id: Conversation identifier.
            role: Either ``user`` or ``assistant``.
            content: Message body.
        """
        if role not in {"user", "assistant"}:
            raise ValueError("memory role must be user or assistant")
        self._execute(
            "INSERT INTO agent_memory (session_id, role, content) VALUES (%s, %s, %s)",
            (session_id, role, content),
        )

    def recent_memory(self, session_id: str, limit: int) -> list[dict[str, str]]:
        """Read recent messages in chronological order.

        Args:
            session_id: Conversation identifier.
            limit: Maximum number of messages to retrieve.

        Returns:
            Ordered dictionaries containing ``role`` and ``content``.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT role, content
                FROM (
                    SELECT memory_id, role, content
                    FROM agent_memory
                    WHERE session_id = %s
                    ORDER BY created_at DESC, memory_id DESC
                    LIMIT %s
                ) recent
                ORDER BY memory_id
                """,
                (session_id, max(1, limit)),
            )
            return [{"role": row[0], "content": row[1]} for row in cursor.fetchall()]
        finally:
            connection.close()

    def add_reflection_record(
        self,
        request_id: str,
        session_id: str,
        record: LoopRecord,
    ) -> None:
        """Persist one Critic cycle for traceability and later analysis.

        Args:
            request_id: Idempotent request correlated with this cycle.
            session_id: Conversation identifier.
            record: Immutable in-process reflection record.
        """
        self._execute(
            """
            INSERT INTO agent_reflection_audit (
                request_id, session_id, iteration, draft_hash,
                composite_score, criterion_scores, verdict, issues, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s)
            """,
            (
                request_id,
                session_id,
                record.iteration,
                record.draft_hash,
                record.composite_score,
                json.dumps(record.criterion_scores),
                record.verdict.value,
                json.dumps(record.issues),
                record.created_at,
            ),
        )

    def save_jira_proposal(
        self,
        request_id: str,
        session_id: str,
        proposal: ProposedJiraAction,
        critic_verdict: Verdict,
    ) -> None:
        """Persist the exact proposed Jira mutation and its Critic verdict.

        Args:
            request_id: Idempotent investigation request identifier.
            session_id: Conversation identifier that produced the proposal.
            proposal: Exact mutation proposed by the Executor.
            critic_verdict: Final Critic outcome for the response draft.
        """
        self._execute(
            """
            INSERT INTO jira_action_proposal (
                request_id, session_id, issue_key, action, payload,
                payload_hash, rationale, critic_verdict
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (request_id) DO NOTHING
            """,
            (
                request_id,
                session_id,
                proposal.issue_key,
                proposal.action.value,
                json.dumps(proposal.payload, ensure_ascii=False),
                proposal.payload_hash,
                proposal.rationale,
                critic_verdict.value,
            ),
        )

    def get_jira_proposal(self, request_id: str) -> StoredJiraProposal:
        """Load one immutable Jira proposal by its investigation request ID.

        Args:
            request_id: Investigation request identifier in the approval file.

        Returns:
            Stored proposal and its final Critic verdict.

        Raises:
            LookupError: If no proposal exists for the request.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT session_id, issue_key, action, payload, rationale,
                       critic_verdict
                FROM jira_action_proposal
                WHERE request_id = %s
                """,
                (request_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise LookupError(f"no Jira proposal exists for request {request_id}")
            payload = row[3] if isinstance(row[3], dict) else json.loads(row[3])
            return StoredJiraProposal(
                request_id=request_id,
                session_id=str(row[0]),
                proposal=ProposedJiraAction(
                    issue_key=str(row[1]),
                    action=JiraAction(str(row[2])),
                    payload=payload,
                    rationale=str(row[4]),
                ),
                critic_verdict=Verdict(str(row[5])),
            )
        finally:
            connection.close()

    def claim_jira_approval(
        self,
        approval: ApprovedJiraAction,
        tool_name: str,
    ) -> None:
        """Atomically mark an approval used before the external Jira call.

        Args:
            approval: Fully validated human approval artifact.
            tool_name: Exact allowlisted Rovo tool selected for execution.

        Raises:
            ValueError: If this approval or proposal has already been used.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT status FROM jira_action_execution
                WHERE approval_id = %s
                FOR UPDATE
                """,
                (approval.approval_id,),
            )
            if cursor.fetchone() is not None:
                raise ValueError("approval_id has already been used")
            cursor.execute(
                """
                SELECT approval_id FROM jira_action_execution
                WHERE request_id = %s AND status IN ('processing', 'completed')
                FOR UPDATE
                """,
                (approval.request_id,),
            )
            if cursor.fetchone() is not None:
                raise ValueError(
                    "this Jira proposal is already processing or completed"
                )
            cursor.execute(
                """
                INSERT INTO jira_action_execution (
                    approval_id, request_id, issue_key, action, payload,
                    payload_hash, approved_by, approved_at, expires_at,
                    status, tool_name
                ) VALUES (
                    %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                    'processing', %s
                )
                """,
                (
                    approval.approval_id,
                    approval.request_id,
                    approval.issue_key,
                    approval.action.value,
                    json.dumps(approval.payload, ensure_ascii=False),
                    approval.payload_hash,
                    approval.approved_by,
                    approval.approved_at,
                    approval.expires_at,
                    tool_name,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_jira_approval(self, result: JiraMutationResult) -> None:
        """Store the successful Rovo response for a claimed approval."""
        self._execute(
            """
            UPDATE jira_action_execution
            SET status = 'completed', response_json = %s::jsonb,
                error_text = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE approval_id = %s AND status = 'processing'
            """,
            (json.dumps(result.response, ensure_ascii=False), result.approval_id),
        )

    def fail_jira_approval(self, approval_id: str, error: str) -> None:
        """Store a sanitized failure for a claimed approval."""
        self._execute(
            """
            UPDATE jira_action_execution
            SET status = 'failed', error_text = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE approval_id = %s AND status = 'processing'
            """,
            (error[:4000], approval_id),
        )

    def _execute(self, sql: str, parameters: tuple[Any, ...]) -> None:
        """Execute and commit one parameterized mutation.

        Args:
            sql: SQL statement with DB-API placeholders.
            parameters: Values bound to the statement.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(sql, parameters)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
