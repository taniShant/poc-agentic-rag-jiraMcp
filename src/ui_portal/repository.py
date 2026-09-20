"""Read Jira ticket views from OpenSearch and approval state from PostgreSQL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import pg8000.dbapi

from src.common.config import OpenSearchConfig, PostgresConfig


@dataclass(frozen=True)
class ApprovalSummary:
    """Latest approval proposal and execution state for one Jira ticket."""

    request_id: str
    action: str
    payload: dict[str, Any]
    payload_hash: str
    rationale: str
    critic_verdict: str
    agent_response: str
    execution_status: str | None
    approved_by: str | None
    approved_at: str | None
    error_text: str | None

    @property
    def approval_needed(self) -> bool:
        """Return whether a human may currently approve this proposal."""
        return self.critic_verdict == "PASS" and self.execution_status not in {
            "processing",
            "completed",
        }


@dataclass(frozen=True)
class PortalTicket:
    """Ticket information rendered by the administrator portal."""

    issue_key: str
    title: str
    problem: str
    resolution: str
    status: str
    status_group: str
    team_name: str
    priority: str
    assignee: str
    category: str
    updated: str
    jira_url: str
    labels: tuple[str, ...]
    approval: ApprovalSummary | None


def status_group(status: str) -> str:
    """Map a Jira status into a stable UI grouping."""
    normalized = status.strip().lower()
    if normalized in {"done", "closed", "resolved", "completed"}:
        return "closed"
    if normalized in {"new", "open", "to do", "todo", "backlog"}:
        return "new"
    return "open"


def team_name(source: dict[str, Any]) -> str:
    """Derive a readable support team from normalized Jira fields."""
    labels = [str(item) for item in source.get("labels", [])]
    for label in labels:
        if label.lower().startswith("team-"):
            return label[5:].replace("-", " ").title()
    category = str(source.get("category", "")).split("/", maxsplit=1)[0].strip()
    category_teams = {
        "identity": "Identity & Access",
        "access": "Identity & Access",
        "endpoint": "Workplace Technology",
        "change": "Release Engineering",
        "network": "Network Operations",
        "security": "Security Operations",
        "database": "Data Platform",
    }
    if category.lower() in category_teams:
        return category_teams[category.lower()]
    assignee = str(source.get("assignee", "")).strip()
    return assignee or "Service Desk"


class PortalRepository:
    """Combine indexed Jira records with approval workflow state."""

    def __init__(
        self,
        opensearch: OpenSearchConfig,
        postgres: PostgresConfig,
        timeout_seconds: int = 15,
    ) -> None:
        """Create HTTP and PostgreSQL connection settings for portal reads."""
        auth = (
            (opensearch.username, opensearch.password)
            if opensearch.username and opensearch.password
            else None
        )
        self._opensearch = opensearch
        self._postgres = postgres
        self._client = httpx.Client(
            base_url=opensearch.url.rstrip("/"),
            auth=auth,
            verify=opensearch.verify_tls,
            timeout=timeout_seconds,
        )

    def _connect(self) -> pg8000.dbapi.Connection:
        """Open a PostgreSQL connection for approval view queries."""
        ssl_context: bool | None = True if self._postgres.ssl else None
        return pg8000.dbapi.connect(
            host=self._postgres.host,
            port=self._postgres.port,
            database=self._postgres.database,
            user=self._postgres.username,
            password=self._postgres.password,
            ssl_context=ssl_context,
            timeout=15,
        )

    def _approval_summaries(self) -> dict[str, ApprovalSummary]:
        """Return the latest proposal and execution for every issue key."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT DISTINCT ON (proposal.issue_key)
                    proposal.issue_key,
                    proposal.request_id,
                    proposal.action,
                    proposal.payload,
                    proposal.payload_hash,
                    proposal.rationale,
                    proposal.critic_verdict,
                    request.response_text,
                    execution.status,
                    execution.approved_by,
                    execution.approved_at,
                    execution.error_text
                FROM jira_action_proposal AS proposal
                LEFT JOIN idempotency_record AS request
                    ON request.request_id = proposal.request_id
                LEFT JOIN LATERAL (
                    SELECT status, approved_by, approved_at, error_text, created_at
                    FROM jira_action_execution
                    WHERE request_id = proposal.request_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) AS execution ON TRUE
                ORDER BY proposal.issue_key, proposal.created_at DESC
                """
            )
            summaries: dict[str, ApprovalSummary] = {}
            for row in cursor.fetchall():
                payload = row[3] if isinstance(row[3], dict) else json.loads(row[3])
                summaries[str(row[0])] = ApprovalSummary(
                    request_id=str(row[1]),
                    action=str(row[2]),
                    payload=payload,
                    payload_hash=str(row[4]),
                    rationale=str(row[5]),
                    critic_verdict=str(row[6]),
                    agent_response=str(row[7] or ""),
                    execution_status=str(row[8]) if row[8] else None,
                    approved_by=str(row[9]) if row[9] else None,
                    approved_at=str(row[10]) if row[10] else None,
                    error_text=str(row[11]) if row[11] else None,
                )
            return summaries
        finally:
            connection.close()

    def _ticket_hits(self, size: int = 500) -> list[dict[str, Any]]:
        """Return normalized Jira documents without vector fields."""
        index = quote(self._opensearch.index_name, safe="")
        response = self._client.post(
            f"/{index}/_search",
            json={
                "size": size,
                "_source": {"excludes": ["embedding"]},
                "query": {"term": {"source_type": "jira_ticket"}},
            },
        )
        response.raise_for_status()
        return response.json().get("hits", {}).get("hits", [])

    @staticmethod
    def _to_ticket(
        source: dict[str, Any],
        approval: ApprovalSummary | None,
    ) -> PortalTicket:
        """Convert one OpenSearch source object to a portal ticket."""
        issue_key = str(source.get("source_id", ""))
        status = str(source.get("status", "Unknown")) or "Unknown"
        return PortalTicket(
            issue_key=issue_key,
            title=str(source.get("title", "Untitled Jira ticket")),
            problem=str(source.get("content", "")),
            resolution=str(source.get("resolution", "")),
            status=status,
            status_group=status_group(status),
            team_name=team_name(source),
            priority=str(source.get("priority", "")) or "Normal",
            assignee=str(source.get("assignee", "")) or "Unassigned",
            category=str(source.get("category", "")) or "General",
            updated=str(source.get("updated", "")) or "Not available",
            jira_url=str(source.get("url", "")),
            labels=tuple(str(item) for item in source.get("labels", [])),
            approval=approval,
        )

    def list_tickets(
        self,
        query: str = "",
        status_filter: str = "all",
    ) -> list[PortalTicket]:
        """Return all indexed Jira tickets with optional UI filtering."""
        approvals = self._approval_summaries()
        tickets = [
            self._to_ticket(hit.get("_source", {}), approvals.get(str(
                hit.get("_source", {}).get("source_id", "")
            )))
            for hit in self._ticket_hits()
        ]
        existing = {ticket.issue_key for ticket in tickets}
        for issue_key, approval in approvals.items():
            if issue_key not in existing:
                tickets.append(
                    self._to_ticket(
                        {
                            "source_id": issue_key,
                            "title": "Jira proposal awaiting indexed ticket data",
                            "content": approval.rationale,
                            "status": "Open",
                        },
                        approval,
                    )
                )
        needle = query.strip().lower()
        if needle:
            tickets = [
                ticket
                for ticket in tickets
                if needle
                in " ".join(
                    [
                        ticket.issue_key,
                        ticket.title,
                        ticket.problem,
                        ticket.team_name,
                    ]
                ).lower()
            ]
        if status_filter in {"new", "open", "closed"}:
            tickets = [
                ticket for ticket in tickets if ticket.status_group == status_filter
            ]
        return sorted(tickets, key=lambda item: item.updated, reverse=True)

    def get_ticket(self, issue_key: str) -> PortalTicket | None:
        """Return one ticket detail view by Jira issue key."""
        approvals = self._approval_summaries()
        index = quote(self._opensearch.index_name, safe="")
        response = self._client.post(
            f"/{index}/_search",
            json={
                "size": 1,
                "_source": {"excludes": ["embedding"]},
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"source_type": "jira_ticket"}},
                            {"term": {"source_id": issue_key}},
                        ]
                    }
                },
            },
        )
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
        if hits:
            return self._to_ticket(hits[0].get("_source", {}), approvals.get(issue_key))
        approval = approvals.get(issue_key)
        if approval is None:
            return None
        return self._to_ticket(
            {
                "source_id": issue_key,
                "title": "Jira proposal awaiting indexed ticket data",
                "content": approval.rationale,
                "status": "Open",
            },
            approval,
        )
