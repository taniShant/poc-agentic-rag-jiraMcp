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
class JiraMcpConfig:
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
class RovoMcpConfig:
    """Atlassian Rovo MCP OAuth 2.1 client settings.

    These settings connect the local Executor to Atlassian's hosted Rovo MCP.
    """

    enabled: bool
    server_url: str
    redirect_uri: str
    token_store: Path
    scopes: str
    allowed_tools: tuple[str, ...]
    oauth_timeout_seconds: int


@dataclass(frozen=True)
class AgentConfig:
    """Local multi-agent session, timeout, and generation settings."""

    memory_messages: int
    default_session_id: str
    request_timeout_seconds: int
    planner_max_tokens: int = 384
    executor_max_tokens: int = 1024
    critic_max_tokens: int = 512
    disable_thinking: bool = True


@dataclass(frozen=True)
class ValidationConfig:
    """Critic-loop thresholds and iteration limits."""

    enabled: bool
    max_refinements: int
    pass_score: float
    min_individual_score: int
    plateau_patience: int = 3
    improvement_epsilon: float = 0.01


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
    jira_mcp: JiraMcpConfig
    rovo_mcp: RovoMcpConfig
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
    jira_mcp = JiraMcpConfig(**_required_section(document, "jiraMcp"))
    rovo_values = document.get("rovoMcp", {})
    if not isinstance(rovo_values, dict):
        raise ValueError("local.json section 'rovoMcp' must be an object")
    token_store_value = str(
        rovo_values.get("token_store", ".local/rovo_oauth_tokens.json")
    )
    token_store = Path(token_store_value).expanduser()
    if not token_store.is_absolute():
        token_store = source_path.parents[2] / token_store
    rovo_mcp = RovoMcpConfig(
        enabled=bool(rovo_values.get("enabled", False)),
        server_url=str(
            rovo_values.get("server_url", "https://mcp.atlassian.com/v2/mcp")
        ),
        redirect_uri=str(
            rovo_values.get(
                "redirect_uri", "http://127.0.0.1:8765/oauth/callback"
            )
        ),
        token_store=token_store.resolve(),
        scopes=str(
            rovo_values.get(
                "scopes",
                "read:jira:agent-interface search:jira:agent-interface",
            )
        ),
        allowed_tools=tuple(
            str(item)
            for item in rovo_values.get(
                "allowed_tools",
                [
                    "atlassianUserInfo",
                    "getAccessibleAtlassianResources",
                    "getJiraIssue",
                    "searchJiraIssuesUsingJql",
                    "discover",
                    "executeRead",
                ],
            )
        ),
        oauth_timeout_seconds=int(rovo_values.get("oauth_timeout_seconds", 300)),
    )
    mcp = McpConfig(**_required_section(document, "mcp"))
    agent_values = _required_section(document, "agent")
    agent = AgentConfig(
        memory_messages=int(agent_values["memory_messages"]),
        default_session_id=str(agent_values["default_session_id"]),
        request_timeout_seconds=int(agent_values["request_timeout_seconds"]),
        planner_max_tokens=int(agent_values.get("planner_max_tokens", 384)),
        executor_max_tokens=int(agent_values.get("executor_max_tokens", 1024)),
        critic_max_tokens=int(agent_values.get("critic_max_tokens", 512)),
        disable_thinking=bool(agent_values.get("disable_thinking", True)),
    )
    validation_values = document.get("validation", {})
    if not isinstance(validation_values, dict):
        raise ValueError("local.json section 'validation' must be an object")
    validation = ValidationConfig(
        enabled=bool(validation_values.get("enabled", False)),
        max_refinements=int(validation_values.get("max_refinements", 2)),
        pass_score=float(validation_values.get("pass_score", 4.0)),
        min_individual_score=int(validation_values.get("min_individual_score", 3)),
        plateau_patience=int(validation_values.get("plateau_patience", 3)),
        improvement_epsilon=float(validation_values.get("improvement_epsilon", 0.01)),
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
    if validation.plateau_patience < 1:
        raise ValueError("validation.plateau_patience must be positive")
    if validation.improvement_epsilon < 0:
        raise ValueError("validation.improvement_epsilon must not be negative")
    if min(agent.planner_max_tokens, agent.executor_max_tokens, agent.critic_max_tokens) < 1:
        raise ValueError("agent role token limits must be positive")
    if jira_mcp.enabled and not all(
        value
        and not value.startswith("replace-")
        and "your-company" not in value
        and "your-email" not in value
        for value in (
            jira_mcp.base_url,
            jira_mcp.email,
            jira_mcp.api_token,
            jira_mcp.project_key,
        )
    ):
        raise ValueError(
            "jiraMcp is enabled but its local.json credentials are placeholders"
        )
    if jira_mcp.enabled and rovo_mcp.enabled:
        raise ValueError("enable either jiraMcp or rovoMcp integration, not both")
    if rovo_mcp.enabled and not rovo_mcp.server_url.startswith("https://"):
        raise ValueError("rovoMcp.server_url must use HTTPS")
    if rovo_mcp.enabled and not rovo_mcp.redirect_uri.startswith(
        ("http://127.0.0.1:", "http://localhost:")
    ):
        raise ValueError("rovoMcp.redirect_uri must use a loopback HTTP address")
    forbidden_rovo_tools = {"executeWrite", "executeDestructive"}
    if forbidden_rovo_tools.intersection(rovo_mcp.allowed_tools):
        raise ValueError(
            "rovoMcp.allowed_tools must not include write/destructive tools"
        )
    if rovo_mcp.oauth_timeout_seconds < 30:
        raise ValueError("rovoMcp.oauth_timeout_seconds must be at least 30")

    return LocalConfig(
        ollama=ollama,
        opensearch=opensearch,
        postgres=postgres,
        jira_mcp=jira_mcp,
        rovo_mcp=rovo_mcp,
        mcp=mcp,
        agent=agent,
        validation=validation,
        source_path=source_path,
    )
