"""OpenSearch index management and multi-source knowledge persistence."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from src.common.config import OpenSearchConfig


class OpenSearchKnowledgeStore:
    """Create and query a shared support-knowledge vector index."""

    def __init__(
        self,
        config: OpenSearchConfig,
        embedding_dimensions: int,
        timeout_seconds: int,
    ) -> None:
        """Initialize the OpenSearch HTTP repository.

        Args:
            config: OpenSearch connection and index settings.
            embedding_dimensions: Vector dimensions used by the mapping.
            timeout_seconds: HTTP request timeout.
        """
        auth = (
            (config.username, config.password)
            if config.username and config.password
            else None
        )
        self._config = config
        self._embedding_dimensions = embedding_dimensions
        self._client = httpx.Client(
            base_url=config.url.rstrip("/"),
            auth=auth,
            verify=config.verify_tls,
            timeout=timeout_seconds,
        )

    @property
    def index_name(self) -> str:
        """Return the configured knowledge index name."""
        return self._config.index_name

    @property
    def index_path(self) -> str:
        """Return the URL-safe OpenSearch index path."""
        return f"/{quote(self.index_name, safe='')}"

    def health(self) -> dict[str, Any]:
        """Return the current OpenSearch cluster health."""
        response = self._client.get("/_cluster/health")
        response.raise_for_status()
        return response.json()

    @staticmethod
    def field_mappings() -> dict[str, Any]:
        """Return fields shared by Jira, Confluence, and known issues."""
        return {
            "document_id": {"type": "keyword"},
            "source_type": {"type": "keyword"},
            "source_id": {"type": "keyword"},
            "title": {"type": "text"},
            "content": {"type": "text"},
            "comments": {"type": "text"},
            "resolution": {"type": "text"},
            "labels": {"type": "keyword"},
            "status": {"type": "keyword"},
            "category": {"type": "keyword"},
            "environment": {"type": "keyword"},
            "space_key": {"type": "keyword"},
            "priority": {"type": "keyword"},
            "assignee": {"type": "keyword"},
            "created": {"type": "date", "ignore_malformed": True},
            "updated": {"type": "date", "ignore_malformed": True},
            "resolved": {"type": "date", "ignore_malformed": True},
            "url": {"type": "keyword", "index": False},
        }

    def ensure_index(self) -> bool:
        """Create the multi-source index or update its additive mappings.

        Returns:
            True only when a new index was created.
        """
        response = self._client.head(self.index_path)
        if response.status_code == 200:
            settings_response = self._client.put(
                f"{self.index_path}/_settings",
                json={"index": {"number_of_replicas": 0}},
            )
            settings_response.raise_for_status()
            mapping_response = self._client.put(
                f"{self.index_path}/_mapping",
                json={"properties": self.field_mappings()},
            )
            mapping_response.raise_for_status()
            return False
        if response.status_code != 404:
            response.raise_for_status()

        properties = self.field_mappings()
        properties["embedding"] = {
            "type": "knn_vector",
            "dimension": self._embedding_dimensions,
            "method": {
                "name": "hnsw",
                "space_type": "cosinesimil",
                "engine": "lucene",
            },
        }
        create_response = self._client.put(
            self.index_path,
            json={
                "settings": {"index": {"knn": True, "number_of_replicas": 0}},
                "mappings": {"dynamic": "strict", "properties": properties},
            },
        )
        create_response.raise_for_status()
        return True

    @staticmethod
    def searchable_text(document: dict[str, Any]) -> str:
        """Build canonical embedding input for any knowledge source."""
        return "\n".join(
            [
                str(document.get("source_type", "")),
                str(document.get("source_id", "")),
                str(document.get("title", "")),
                str(document.get("content", "")),
                str(document.get("resolution", "")),
                str(document.get("status", "")),
                str(document.get("category", "")),
                str(document.get("environment", "")),
                " ".join(str(label) for label in document.get("labels", [])),
                "\n".join(str(item) for item in document.get("comments", [])),
            ]
        )

    def upsert_document(
        self,
        document: dict[str, Any],
        embedding: list[float],
        refresh: bool = False,
    ) -> None:
        """Insert or replace one normalized knowledge document."""
        document_id = str(document["document_id"])
        response = self._client.put(
            f"{self.index_path}/_doc/{quote(document_id, safe='')}",
            params={"refresh": "true" if refresh else "false"},
            json={**document, "embedding": embedding},
        )
        response.raise_for_status()

    def existing_document_ids(self, document_ids: list[str]) -> set[str]:
        """Return the subset of document IDs already stored in the index."""
        if not document_ids:
            return set()
        response = self._client.post(
            f"{self.index_path}/_mget",
            json={"ids": document_ids},
        )
        response.raise_for_status()
        return {
            str(document["_id"])
            for document in response.json().get("docs", [])
            if document.get("found")
        }

    def keyword_search(self, query: str, size: int) -> list[dict[str, Any]]:
        """Return raw BM25-ranked knowledge hits."""
        response = self._client.post(
            f"{self.index_path}/_search",
            json={
                "size": size,
                "_source": {"excludes": ["embedding"]},
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": [
                            "source_id^4",
                            "title^3",
                            "content^2",
                            "comments^2",
                            "resolution^2",
                            "labels",
                            "status",
                            "category",
                        ],
                    }
                },
            },
        )
        response.raise_for_status()
        return response.json().get("hits", {}).get("hits", [])

    def vector_search(
        self, embedding: list[float], size: int
    ) -> list[dict[str, Any]]:
        """Return raw k-NN-ranked knowledge hits."""
        response = self._client.post(
            f"{self.index_path}/_search",
            json={
                "size": size,
                "_source": {"excludes": ["embedding"]},
                "query": {"knn": {"embedding": {"vector": embedding, "k": size}}},
            },
        )
        response.raise_for_status()
        return response.json().get("hits", {}).get("hits", [])

    def count_by_source(self) -> dict[str, int]:
        """Return indexed document counts grouped by source type."""
        response = self._client.post(
            f"{self.index_path}/_search",
            json={
                "size": 0,
                "aggs": {
                    "sources": {
                        "terms": {"field": "source_type", "size": 20}
                    }
                },
            },
        )
        response.raise_for_status()
        buckets = (
            response.json()
            .get("aggregations", {})
            .get("sources", {})
            .get("buckets", [])
        )
        return {str(item["key"]): int(item["doc_count"]) for item in buckets}
