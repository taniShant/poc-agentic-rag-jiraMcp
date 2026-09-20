"""Ingest Jira, Confluence, and known-issue knowledge into OpenSearch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from src.common.config import load_config
from src.mcp.jira_client import JiraClient
from src.vectorDb.embeddings import OllamaEmbeddings
from src.vectorDb.opensearch_store import OpenSearchKnowledgeStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_TICKETS = PROJECT_ROOT / "scripts/jira/golden_tickets.preview.json"
DEFAULT_CONFLUENCE = PROJECT_ROOT / "scripts/jira/dummy_confluence_runbooks.json"
DEFAULT_KNOWN_ISSUES = PROJECT_ROOT / "scripts/jira/dummy_known_issues.json"


def _text_list(items: list[Any]) -> str:
    """Join a list of values as readable bullet text."""
    return "\n".join(f"- {item}" for item in items)


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Read and validate an array of JSON knowledge records."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path} must contain an array of JSON objects")
    return value


def normalize_jira_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    """Convert a live normalized Jira ticket to the shared knowledge schema."""
    issue_key = str(ticket["issue_key"])
    return {
        "document_id": f"jira:{issue_key}",
        "source_type": "jira_ticket",
        "source_id": issue_key,
        "title": str(ticket.get("summary", "")),
        "content": str(ticket.get("description", "")),
        "comments": [str(item) for item in ticket.get("comments", [])],
        "resolution": str(ticket.get("resolution", "")),
        "labels": [str(item) for item in ticket.get("labels", [])],
        "status": str(ticket.get("status", "")),
        "category": str(ticket.get("issue_type", "")),
        "environment": "",
        "space_key": "",
        "priority": str(ticket.get("priority", "")),
        "assignee": str(ticket.get("assignee", "")),
        "created": ticket.get("created"),
        "updated": ticket.get("updated"),
        "resolved": ticket.get("resolved"),
        "url": str(ticket.get("url", "")),
    }


def normalize_golden_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    """Convert one offline golden Jira fixture to the knowledge schema."""
    source_id = str(ticket["external_id"])
    content = "\n\n".join(
        [
            f"Request: {ticket.get('request', '')}",
            f"Business justification: {ticket.get('business_justification', '')}",
            "Checks:\n" + _text_list(ticket.get("checks", [])),
            "Resolution steps:\n" + _text_list(ticket.get("resolution_steps", [])),
            "Validation:\n" + _text_list(ticket.get("validation", [])),
        ]
    )
    return {
        "document_id": f"jira-sample:{source_id}",
        "source_type": "jira_ticket",
        "source_id": source_id,
        "title": str(ticket.get("summary", "")),
        "content": content,
        "comments": [],
        "resolution": str(ticket.get("resolution_code", "")),
        "labels": [str(item) for item in ticket.get("labels", [])],
        "status": "Done",
        "category": str(ticket.get("category", "")),
        "environment": str(ticket.get("environment", "")),
        "space_key": "",
        "priority": "",
        "assignee": "",
        "created": ticket.get("opened_date"),
        "updated": ticket.get("completed_date"),
        "resolved": ticket.get("completed_date"),
        "url": "",
    }


def normalize_confluence_page(page: dict[str, Any]) -> dict[str, Any]:
    """Convert one exported Confluence page to the knowledge schema."""
    page_id = str(page["page_id"])
    content_type = str(page.get("content_type", "runbook"))
    return {
        "document_id": f"confluence:{page_id}",
        "source_type": f"confluence_{content_type}",
        "source_id": page_id,
        "title": str(page.get("title", "")),
        "content": str(page.get("content", "")),
        "comments": [],
        "resolution": "",
        "labels": [str(item) for item in page.get("labels", [])],
        "status": str(page.get("status", "current")),
        "category": str(page.get("category", "")),
        "environment": str(page.get("environment", "")),
        "space_key": str(page.get("space_key", "")),
        "priority": "",
        "assignee": str(page.get("owner", "")),
        "created": page.get("created"),
        "updated": page.get("updated"),
        "resolved": None,
        "url": str(page.get("url", "")),
    }


def normalize_known_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Convert one known-issue record to the shared knowledge schema."""
    source_id = str(issue["known_issue_id"])
    content = "\n\n".join(
        [
            f"Symptoms: {issue.get('symptoms', '')}",
            f"Affected versions: {issue.get('affected_versions', '')}",
            f"Root cause: {issue.get('root_cause', '')}",
            f"Workaround: {issue.get('workaround', '')}",
        ]
    )
    return {
        "document_id": f"known-issue:{source_id}",
        "source_type": "known_issue",
        "source_id": source_id,
        "title": str(issue.get("title", "")),
        "content": content,
        "comments": [],
        "resolution": str(issue.get("permanent_fix", "")),
        "labels": [str(item) for item in issue.get("labels", [])],
        "status": str(issue.get("status", "")),
        "category": str(issue.get("product", "")),
        "environment": str(issue.get("environment", "")),
        "space_key": "",
        "priority": str(issue.get("severity", "")),
        "assignee": str(issue.get("owner", "")),
        "created": issue.get("created"),
        "updated": issue.get("updated"),
        "resolved": issue.get("resolved"),
        "url": str(issue.get("url", "")),
    }


def _normalize_file(
    path: Path, normalizer: Callable[[dict[str, Any]], dict[str, Any]]
) -> list[dict[str, Any]]:
    """Read a JSON fixture and normalize every record."""
    return [normalizer(item) for item in _read_records(path)]


def _index_documents(
    documents: list[dict[str, Any]],
    store: OpenSearchKnowledgeStore,
    embeddings: OllamaEmbeddings,
    batch_size: int,
) -> None:
    """Embed and upsert normalized documents, refreshing after the final write."""
    for batch_start in range(0, len(documents), batch_size):
        batch = documents[batch_start : batch_start + batch_size]
        vectors = embeddings.embed_many(
            [store.searchable_text(document) for document in batch]
        )
        for offset, (document, vector) in enumerate(zip(batch, vectors), start=1):
            position = batch_start + offset
            store.upsert_document(
                document, vector, refresh=position == len(documents)
            )
            print(
                f"indexed {document['source_type']} "
                f"{document['source_id']}  {document['title']}",
                flush=True,
            )


def main() -> None:
    """Select sources, normalize records, embed them, and upsert OpenSearch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="ci-cd/env/local.json", help="Path to local.json"
    )
    parser.add_argument(
        "--source",
        choices=("jira", "sample-jira", "confluence", "known-issues", "samples", "all"),
        default="jira",
        help="Knowledge source to ingest",
    )
    parser.add_argument(
        "--jira-file", type=Path, default=DEFAULT_GOLDEN_TICKETS
    )
    parser.add_argument(
        "--confluence-file", type=Path, default=DEFAULT_CONFLUENCE
    )
    parser.add_argument(
        "--known-issues-file", type=Path, default=DEFAULT_KNOWN_ISSUES
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=8,
        help="Number of documents sent to Ollama per embedding request",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Do not re-embed documents already present in OpenSearch",
    )
    arguments = parser.parse_args()
    if arguments.embedding_batch_size < 1:
        parser.error("--embedding-batch-size must be positive")
    config = load_config(arguments.config)
    embeddings = OllamaEmbeddings(
        config.ollama, timeout_seconds=config.agent.request_timeout_seconds
    )
    store = OpenSearchKnowledgeStore(
        config.opensearch,
        config.ollama.embedding_dimensions,
        timeout_seconds=config.agent.request_timeout_seconds,
    )
    store.ensure_index()

    documents: list[dict[str, Any]] = []
    if arguments.source in {"jira", "all"}:
        jira = JiraClient(
            config.jira, timeout_seconds=config.agent.request_timeout_seconds
        )
        tickets = jira.search(
            config.jira.sync_jql, max_results=config.jira.sync_limit
        )
        documents.extend(normalize_jira_ticket(ticket) for ticket in tickets)
    if arguments.source in {"sample-jira", "samples"}:
        documents.extend(
            _normalize_file(arguments.jira_file, normalize_golden_ticket)
        )
    if arguments.source in {"confluence", "samples", "all"}:
        documents.extend(
            _normalize_file(arguments.confluence_file, normalize_confluence_page)
        )
    if arguments.source in {"known-issues", "samples", "all"}:
        documents.extend(
            _normalize_file(arguments.known_issues_file, normalize_known_issue)
        )
    if not documents:
        print("No source records matched the ingestion request.")
        return

    if arguments.skip_existing:
        existing = store.existing_document_ids(
            [str(document["document_id"]) for document in documents]
        )
        documents = [
            document
            for document in documents
            if str(document["document_id"]) not in existing
        ]
        print(f"Skipped {len(existing)} existing documents.", flush=True)
        if not documents:
            print(
                f"All requested documents already exist: "
                f"{json.dumps(store.count_by_source(), sort_keys=True)}"
            )
            return

    _index_documents(
        documents, store, embeddings, arguments.embedding_batch_size
    )
    print(
        f"Synchronized {len(documents)} documents into {store.index_name}: "
        f"{json.dumps(store.count_by_source(), sort_keys=True)}"
    )


if __name__ == "__main__":
    main()
