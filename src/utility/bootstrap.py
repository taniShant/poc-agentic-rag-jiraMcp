"""Validate local dependencies and initialize PostgreSQL and OpenSearch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.common.config import load_config
from src.memory.postgres import LocalStorage
from src.vectorDb.embeddings import OllamaEmbeddings
from src.vectorDb.opensearch_store import OpenSearchKnowledgeStore

MIGRATION_DIRECTORY = (
    Path(__file__).resolve().parents[2] / "scripts" / "postgresDb"
)


def main() -> None:
    """Run dependency health checks and idempotent local schema initialization."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="ci-cd/env/local.json", help="Path to local.json"
    )
    arguments = parser.parse_args()
    config = load_config(arguments.config)

    storage = LocalStorage(config.postgres)
    migrations = storage.migrate(MIGRATION_DIRECTORY)
    print(f"PostgreSQL migrations: {', '.join(migrations)}")

    embeddings = OllamaEmbeddings(
        config.ollama, timeout_seconds=config.agent.request_timeout_seconds
    )
    tags = embeddings.health()
    installed_models = [model.get("name") for model in tags.get("models", [])]
    print(f"Ollama models: {json.dumps(installed_models)}")
    embeddings.embed("local bootstrap dimension check")
    print(
        "Ollama embedding dimensions: "
        f"{config.ollama.embedding_dimensions} (verified)"
    )

    store = OpenSearchKnowledgeStore(
        config.opensearch,
        config.ollama.embedding_dimensions,
        timeout_seconds=config.agent.request_timeout_seconds,
    )
    created = store.ensure_index()
    health = store.health()
    print(
        f"OpenSearch cluster={health.get('cluster_name')} "
        f"status={health.get('status')} index={store.index_name} "
        f"created={created}"
    )

    if config.jira.enabled:
        print("Jira MCP: enabled")
    else:
        print("Jira MCP: disabled until credentials are set in local.json")


if __name__ == "__main__":
    main()
