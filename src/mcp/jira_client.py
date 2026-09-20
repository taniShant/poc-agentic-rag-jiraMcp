"""Small Jira Cloud REST client used behind the local MCP boundary."""

from __future__ import annotations

from typing import Any

import httpx

from src.common.config import JiraConfig


def _adf_text(value: Any) -> str:
    """Extract plain text recursively from Atlassian Document Format.

    Args:
        value: Jira field value that may contain nested ADF nodes.

    Returns:
        Flattened human-readable text.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_adf_text(item) for item in value)))
    if isinstance(value, dict):
        own_text = value.get("text", "")
        child_text = _adf_text(value.get("content", []))
        return "\n".join(part for part in (own_text, child_text) if part)
    return ""


class JiraClient:
    """Call Jira Cloud REST API v3 using a local user's API token."""

    def __init__(self, config: JiraConfig, timeout_seconds: int = 30) -> None:
        """Initialize an authenticated Jira client.

        Args:
            config: Jira settings loaded from ``local.json``.
            timeout_seconds: HTTP request timeout.

        Raises:
            RuntimeError: If Jira integration is disabled.
        """
        if not config.enabled:
            raise RuntimeError("Jira integration is disabled in local.json")
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            auth=(config.email, config.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout_seconds,
        )

    def search(self, jql: str, max_results: int = 25) -> list[dict[str, Any]]:
        """Search Jira issues and return normalized ticket dictionaries.

        Args:
            jql: Jira Query Language expression.
            max_results: Maximum results from 1 through 100.

        Returns:
            Normalized Jira tickets.
        """
        response = self._client.get(
            "/rest/api/3/search/jql",
            params={
                "jql": jql,
                "maxResults": min(max(max_results, 1), 100),
                "fields": (
                    "summary,description,status,issuetype,project,labels,"
                    "created,updated,resolutiondate,resolution,comment,priority,assignee"
                ),
            },
        )
        response.raise_for_status()
        return [self._normalize_issue(issue) for issue in response.json().get("issues", [])]

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Retrieve one Jira issue by key.

        Args:
            issue_key: Jira issue key such as ``DEMO-12``.

        Returns:
            Normalized Jira ticket.
        """
        response = self._client.get(
            f"/rest/api/3/issue/{issue_key}",
            params={
                "fields": (
                    "summary,description,status,issuetype,project,labels,"
                    "created,updated,resolutiondate,resolution,comment,priority,assignee"
                )
            },
        )
        response.raise_for_status()
        return self._normalize_issue(response.json())

    def create_issue(
        self,
        summary: str,
        description: str,
        issue_type: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Create one Jira issue for an explicit local seed operation.

        Args:
            summary: Issue summary.
            description: Plain-text issue description.
            issue_type: Jira issue type, for example ``Task`` or ``Bug``.
            labels: Labels used for traceability and idempotent seeding.

        Returns:
            Jira creation response containing the new key and ID.

        Raises:
            PermissionError: If Jira is configured as read-only.
        """
        if self._config.read_only:
            raise PermissionError("Set jira.read_only=false to create dummy tickets")
        response = self._client.post(
            "/rest/api/3/issue",
            json={
                "fields": {
                    "project": {"key": self._config.project_key},
                    "summary": summary,
                    "issuetype": {"name": issue_type},
                    "labels": labels,
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": description}],
                            }
                        ],
                    },
                }
            },
        )
        response.raise_for_status()
        return response.json()

    def _normalize_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        """Convert a Jira REST issue into the OpenSearch/MCP ticket shape.

        Args:
            issue: Raw Jira issue response.

        Returns:
            Stable normalized ticket dictionary.
        """
        fields = issue.get("fields", {})
        issue_key = str(issue.get("key", ""))
        project = fields.get("project") or {}
        status = fields.get("status") or {}
        issue_type = fields.get("issuetype") or {}
        resolution = fields.get("resolution") or {}
        priority = fields.get("priority") or {}
        assignee = fields.get("assignee") or {}
        comments = (fields.get("comment") or {}).get("comments") or []
        return {
            "issue_key": issue_key,
            "summary": str(fields.get("summary") or ""),
            "description": _adf_text(fields.get("description")),
            "status": str(status.get("name") or ""),
            "issue_type": str(issue_type.get("name") or ""),
            "project_key": str(project.get("key") or self._config.project_key),
            "labels": [str(label) for label in fields.get("labels") or []],
            "comments": [
                text
                for comment in comments
                if (text := _adf_text(comment.get("body")))
            ],
            "resolution": str(resolution.get("name") or ""),
            "priority": str(priority.get("name") or ""),
            "assignee": str(assignee.get("displayName") or ""),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "resolved": fields.get("resolutiondate"),
            "url": f"{self._config.base_url.rstrip('/')}/browse/{issue_key}",
        }
