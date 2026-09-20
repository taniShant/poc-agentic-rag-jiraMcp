"""Typed configuration loaded exclusively from ``local.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OllamaConfig:
    """Ollama chat and embedding model settings."""

    base_url: str
    chat_model: str
    embedding_model: str
    embedding_dimensions: int
    temperature: float


@dataclass(frozen=True)
class OpenSearchConfig:
    """Local OpenSearch connection and hybrid-ranking settings."""

    url: str
    index_name: str
    username: str
    password: str
    verify_tls: bool
    keyword_weight: float
    vector_weight: float


@dataclass(frozen=True)
class PostgresConfig:
    """PostgreSQL connection settings for memory and idempotency."""

    host: str
    port: int
    database: str
    username: str
    password: str
    ssl: bool


@dataclass(frozen=True)
class JiraConfig:
    """Jira Cloud REST settings exposed through the local MCP server."""

    enabled: bool
    base_url: str
    email: str
    api_token: str
    project_key: str
    read_only: bool
    sync_jql: str
    sync_limit: int


@dataclass(frozen=True)
class AgentConfig:
    """Local agent session and timeout settings."""

    memory_messages: int
    default_session_id: str
    request_timeout_seconds: int


@dataclass(frozen=True)
class ValidationConfig:
    """Critic-loop thresholds and iteration limits."""

    enabled: bool
    max_refinements: int
    pass_score: float
    min_individual_score: int


@dataclass(frozen=True)
class McpConfig:
    """Local MCP subprocess transport settings."""

    transport: str
    startup_timeout_seconds: int


@dataclass(frozen=True)
class LocalConfig:
    """Complete local application configuration."""

    ollama: OllamaConfig
    opensearch: OpenSearchConfig
    postgres: PostgresConfig
    jira: JiraConfig
    mcp: McpConfig
    agent: AgentConfig
    validation: ValidationConfig
    source_path: Path


def _required_section(document: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a required object-valued configuration section.

    Args:
        document: Parsed JSON configuration document.
        name: Required top-level section name.

    Returns:
        The requested configuration mapping.

    Raises:
        ValueError: If the section is absent or is not a JSON object.
    """
    value = document.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"local.json section {name!r} must be an object")
    return value


def load_config(path: str | Path = "ci-cd/env/local.json") -> LocalConfig:
    """Load and validate all local runtime settings from one JSON file.

    Args:
        path: Path to the local JSON configuration file.

    Returns:
        Immutable typed local configuration.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If required values are missing or inconsistent.
    """
    source_path = Path(path).expanduser().resolve()
    document = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("local.json must contain a JSON object")

    ollama = OllamaConfig(**_required_section(document, "ollama"))
    opensearch = OpenSearchConfig(**_required_section(document, "opensearch"))
    postgres = PostgresConfig(**_required_section(document, "postgres"))
    jira = JiraConfig(**_required_section(document, "jira"))
    mcp = McpConfig(**_required_section(document, "mcp"))
    agent = AgentConfig(**_required_section(document, "agent"))
    validation = ValidationConfig(
        **document.get(
            "validation",
            {
                "enabled": False,
                "max_refinements": 2,
                "pass_score": 4.0,
                "min_individual_score": 3,
            },
        )
    )

    if ollama.embedding_dimensions < 1:
        raise ValueError("ollama.embedding_dimensions must be positive")
    if not 0 <= opensearch.keyword_weight <= 1:
        raise ValueError("opensearch.keyword_weight must be between 0 and 1")
    if not 0 <= opensearch.vector_weight <= 1:
        raise ValueError("opensearch.vector_weight must be between 0 and 1")
    if abs(opensearch.keyword_weight + opensearch.vector_weight - 1.0) > 1e-9:
        raise ValueError("OpenSearch keyword and vector weights must total 1.0")
    if mcp.transport != "stdio":
        raise ValueError("the local Jira MCP server currently requires stdio transport")
    if validation.max_refinements < 0 or validation.max_refinements > 8:
        raise ValueError("validation.max_refinements must be between 0 and 8")
    if not 1 <= validation.pass_score <= 5:
        raise ValueError("validation.pass_score must be between 1 and 5")
    if not 1 <= validation.min_individual_score <= 5:
        raise ValueError("validation.min_individual_score must be between 1 and 5")
    if jira.enabled and not all(
        value
        and not value.startswith("replace-")
        and "your-company" not in value
        and "your-email" not in value
        for value in (jira.base_url, jira.email, jira.api_token, jira.project_key)
    ):
        raise ValueError("Jira is enabled but its local.json credentials are placeholders")

    return LocalConfig(
        ollama=ollama,
        opensearch=opensearch,
        postgres=postgres,
        jira=jira,
        mcp=mcp,
        agent=agent,
        validation=validation,
        source_path=source_path,
    )
