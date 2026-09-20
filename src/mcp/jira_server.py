"""Local stdio MCP server exposing constrained Jira Cloud tools."""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.common.config import load_config
from src.mcp.jira_client import JiraClient

MCP = FastMCP(name="local-atlassian-jira")
_JIRA_CLIENT: JiraClient | None = None


def _client() -> JiraClient:
    """Return the initialized Jira client.

    Returns:
        Jira REST client configured by ``main``.

    Raises:
        RuntimeError: If the MCP server has not been initialized.
    """
    if _JIRA_CLIENT is None:
        raise RuntimeError("Jira MCP server is not initialized")
    return _JIRA_CLIENT


@MCP.tool()
def search_jira_tickets(jql: str, max_results: int = 20) -> dict[str, Any]:
    """Search Jira Cloud using a read-only JQL query.

    Args:
        jql: Jira Query Language expression, preferably scoped to the configured project.
        max_results: Maximum number of tickets from 1 through 100.

    Returns:
        Normalized matching Jira tickets.
    """
    tickets = _client().search(jql, max_results=max_results)
    return {"count": len(tickets), "tickets": tickets}


@MCP.tool()
def get_jira_ticket(issue_key: str) -> dict[str, Any]:
    """Retrieve complete details for one Jira Cloud ticket.

    Args:
        issue_key: Jira issue key such as ``DEMO-12``.

    Returns:
        Normalized Jira ticket details.
    """
    return _client().get_issue(issue_key)


def main() -> None:
    """Load ``local.json`` and serve Jira tools over MCP stdio."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="ci-cd/env/local.json", help="Path to local.json"
    )
    arguments = parser.parse_args()
    config = load_config(arguments.config)

    global _JIRA_CLIENT
    _JIRA_CLIENT = JiraClient(
        config.jira_mcp, timeout_seconds=config.agent.request_timeout_seconds
    )
    MCP.run(transport="stdio")


if __name__ == "__main__":
    main()
