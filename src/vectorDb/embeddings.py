"""Generate local vector embeddings through Ollama."""

from __future__ import annotations

from typing import Any

import httpx

from src.common.config import OllamaConfig


class OllamaEmbeddings:
    """Generate and validate embeddings through Ollama's API."""

    def __init__(self, config: OllamaConfig, timeout_seconds: int) -> None:
        """Initialize the embedding client.

        Args:
            config: Ollama embedding configuration.
            timeout_seconds: HTTP request timeout.
        """
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"), timeout=timeout_seconds
        )

    def embed(self, text: str) -> list[float]:
        """Return one embedding with the configured dimensions.

        Args:
            text: Text to embed.

        Returns:
            Numeric embedding vector.

        Raises:
            RuntimeError: If Ollama returns no vector or a wrong-sized vector.
        """
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Return validated embeddings for a non-empty text batch.

        Args:
            texts: Ordered input strings to embed in one Ollama request.

        Returns:
            Embeddings in the same order as the input strings.

        Raises:
            ValueError: If no input text is supplied.
            RuntimeError: If Ollama returns a wrong count or vector dimension.
        """
        if not texts:
            raise ValueError("at least one embedding input is required")
        response = self._client.post(
            "/api/embed",
            json={
                "model": self._config.embedding_model,
                "input": texts,
                "dimensions": self._config.embedding_dimensions,
            },
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings", [])
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"Ollama returned {len(embeddings)} embeddings for {len(texts)} inputs"
            )
        vectors = [[float(value) for value in vector] for vector in embeddings]
        for vector in vectors:
            if len(vector) != self._config.embedding_dimensions:
                raise RuntimeError(
                    "Ollama embedding dimension mismatch: "
                    f"expected {self._config.embedding_dimensions}, got {len(vector)}"
                )
        return vectors

    def health(self) -> dict[str, Any]:
        """Return Ollama's installed-model response."""
        response = self._client.get("/api/tags")
        response.raise_for_status()
        return response.json()
