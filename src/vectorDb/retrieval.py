"""Hybrid keyword and vector retrieval across support knowledge sources."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.common.config import OpenSearchConfig
from src.vectorDb.embeddings import OllamaEmbeddings
from src.vectorDb.opensearch_store import OpenSearchKnowledgeStore


class HybridKnowledgeRetriever:
    """Combine BM25 and k-NN knowledge rankings with weighted RRF."""

    def __init__(
        self,
        config: OpenSearchConfig,
        embeddings: OllamaEmbeddings,
        embedding_dimensions: int,
        timeout_seconds: int,
    ) -> None:
        """Initialize retrieval dependencies and ranking weights."""
        self._config = config
        self._embeddings = embeddings
        self._store = OpenSearchKnowledgeStore(
            config, embedding_dimensions, timeout_seconds
        )

    @property
    def index_name(self) -> str:
        """Return the configured OpenSearch index name."""
        return self._store.index_name

    def ensure_index(self) -> bool:
        """Ensure that the knowledge index exists."""
        return self._store.ensure_index()

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve Jira, Confluence, and known-issue knowledge."""
        safe_limit = min(max(limit, 1), 25)
        candidate_count = min(max(safe_limit * 4, 20), 100)
        vector = self._embeddings.embed(query)
        keyword_hits = self._store.keyword_search(query, candidate_count)
        vector_hits = self._store.vector_search(vector, candidate_count)
        return self._reciprocal_rank_fusion(
            keyword_hits,
            vector_hits,
            self._config.keyword_weight,
            self._config.vector_weight,
            safe_limit,
        )

    @staticmethod
    def _reciprocal_rank_fusion(
        keyword_hits: list[dict[str, Any]],
        vector_hits: list[dict[str, Any]],
        keyword_weight: float,
        vector_weight: float,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Combine ranked hit lists using weighted reciprocal-rank fusion."""
        scores: dict[str, float] = defaultdict(float)
        documents: dict[str, dict[str, Any]] = {}
        rank_constant = 60
        for hits, weight in (
            (keyword_hits, keyword_weight),
            (vector_hits, vector_weight),
        ):
            for rank, hit in enumerate(hits, start=1):
                identifier = str(hit.get("_id"))
                scores[identifier] += weight / (rank_constant + rank)
                documents[identifier] = dict(hit.get("_source", {}))
        ordered_ids = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [
            {**documents[identifier], "hybrid_score": round(scores[identifier], 8)}
            for identifier in ordered_ids
        ]
