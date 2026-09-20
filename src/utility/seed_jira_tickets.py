"""Explicitly create idempotent demonstration tickets in Jira Cloud."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.common.config import load_config
from src.mcp.jira_client import JiraClient


def main() -> None:
    """Create missing labeled Jira demonstration tickets from the fixture file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="ci-cd/env/local.json", help="Path to local.json"
    )
    arguments = parser.parse_args()
    config = load_config(arguments.config)
    client = JiraClient(config.jira, timeout_seconds=config.agent.request_timeout_seconds)
    fixture_path = Path(__file__).with_name("dummy_tickets.json")
    tickets = json.loads(fixture_path.read_text(encoding="utf-8"))

    for ticket in tickets:
        external_id = str(ticket["external_id"])
        jql = f'project = "{config.jira.project_key}" AND labels = "{external_id}"'
        existing = client.search(jql, max_results=1)
        if existing:
            print(f"exists  {existing[0]['issue_key']}  {ticket['summary']}")
            continue
        created = client.create_issue(
            summary=str(ticket["summary"]),
            description=str(ticket["description"]),
            issue_type=str(ticket["issue_type"]),
            labels=["local-agent-demo", external_id],
        )
        print(f"created {created['key']}  {ticket['summary']}")


if __name__ == "__main__":
    main()
