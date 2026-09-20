"""Run the local Planner–Executor–Critic workflow and its data services."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters, stdio_client
from strands import tool
from strands.models.ollama import OllamaModel
from strands.tools.mcp import MCPClient

from src.agents.step_2_orchestrator import MultiAgentOrchestrator
from src.agents.step_3_planner import PlannerAgent
from src.agents.step_4_executor import ExecutorAgent
from src.agents.step_5_critic import CriticAgent
from src.common.config import LocalConfig, load_config
from src.memory.postgres import LocalStorage
from src.mcp.rovo_mcp_client import create_rovo_mcp_client
from src.vectorDb.embeddings import OllamaEmbeddings
from src.vectorDb.retrieval import HybridKnowledgeRetriever

_RETRIEVER: HybridKnowledgeRetriever | None = None


@tool
def search_support_knowledge(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search all indexed support knowledge with hybrid ranking.

    Args:
        query: Natural-language or keyword ticket search.
        limit: Maximum number of results from 1 through 25.

    Returns:
        Ranked Jira, Confluence, and known-issue documents.

    Raises:
        RuntimeError: If the local search service has not been initialized.
    """
    if _RETRIEVER is None:
        raise RuntimeError("Local search is not initialized")
    return _RETRIEVER.search(query, limit=limit)


def _conversation_prompt(memory: list[dict[str, str]], current_prompt: str) -> str:
    """Combine durable recent memory with the current user prompt.

    Args:
        memory: Chronologically ordered stored messages.
        current_prompt: New user request.

    Returns:
        Prompt containing labeled prior context and the current request.
    """
    if not memory:
        return current_prompt
    history = "\n".join(
        f"{message['role'].upper()}: {message['content']}" for message in memory
    )
    return (
        "Recent conversation memory follows. Treat it as context, not as tool "
        f"evidence.\n\n{history}\n\nCURRENT USER REQUEST: {current_prompt}"
    )


def _model(config: LocalConfig, max_tokens: int) -> OllamaModel:
    """Build one role-specific Ollama model adapter with a bounded timeout."""
    additional_args = {"think": False} if config.agent.disable_thinking else None
    return OllamaModel(
        host=config.ollama.base_url,
        model_id=config.ollama.chat_model,
        temperature=config.ollama.temperature,
        max_tokens=max_tokens,
        ollama_client_args={"timeout": config.agent.request_timeout_seconds},
        additional_args=additional_args,
    )


def _invoke(
    config: LocalConfig,
    prompt: str,
    mcp_client: MCPClient | None,
    storage: LocalStorage,
    session_id: str,
    request_id: str,
) -> str:
    """Run Planner, tool-enabled Executor, and optional Critic reflection.

    Args:
        config: Complete local application configuration.
        prompt: Prompt including selected conversation memory.
        mcp_client: Connected Jira MCP client, or ``None`` when Jira is disabled.
        storage: PostgreSQL repository used for reflection audit records.
        session_id: Durable conversation identifier.
        request_id: Idempotency identifier correlated with audit records.

    Returns:
        Final answer text, including an escalation marker when applicable.
    """
    tools: list[Any] = [search_support_knowledge]
    if mcp_client is not None:
        tools.extend(mcp_client.list_tools_sync())

    planner = PlannerAgent(_model(config, config.agent.planner_max_tokens))
    executor = ExecutorAgent(
        _model(config, config.agent.executor_max_tokens),
        tools,
    )
    critic = CriticAgent(
        _model(config, config.agent.critic_max_tokens),
        config.validation,
    )
    orchestrator = MultiAgentOrchestrator(
        planner,
        executor,
        critic,
        config.validation,
        audit_sink=lambda record: storage.add_reflection_record(
            request_id,
            session_id,
            record,
        ),
    )
    return orchestrator.run(prompt).render()


def run_request(
    config: LocalConfig,
    session_id: str,
    request_id: str,
    prompt: str,
) -> str:
    """Run one idempotent multi-agent request and persist its memory.

    Args:
        config: Complete local application configuration.
        session_id: Durable conversation identifier.
        request_id: Idempotency key for this exact request.
        prompt: User request text.

    Returns:
        Cached or newly generated response.
    """
    storage = LocalStorage(config.postgres)
    cached = storage.begin_request(request_id, session_id, prompt)
    if cached is not None:
        return cached

    try:
        memory = storage.recent_memory(session_id, config.agent.memory_messages)
        augmented_prompt = _conversation_prompt(memory, prompt)
        if config.rovo_mcp.enabled:
            mcp_client = create_rovo_mcp_client(
                config.rovo_mcp,
                startup_timeout_seconds=config.mcp.startup_timeout_seconds,
                request_timeout_seconds=config.agent.request_timeout_seconds,
            )
            with mcp_client:
                response = _invoke(
                    config,
                    augmented_prompt,
                    mcp_client,
                    storage,
                    session_id,
                    request_id,
                )
        elif config.jira_mcp.enabled:
            server = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "src.mcp.jira_server",
                    "--config",
                    str(config.source_path),
                ],
                cwd=str(Path(__file__).resolve().parents[2]),
            )
            mcp_client = MCPClient(
                lambda: stdio_client(server),
                startup_timeout=config.mcp.startup_timeout_seconds,
            )
            with mcp_client:
                response = _invoke(
                    config,
                    augmented_prompt,
                    mcp_client,
                    storage,
                    session_id,
                    request_id,
                )
        else:
            response = _invoke(
                config,
                augmented_prompt,
                None,
                storage,
                session_id,
                request_id,
            )

        storage.add_memory(session_id, "user", prompt)
        storage.add_memory(session_id, "assistant", response)
        storage.complete_request(request_id, response)
        return response
    except Exception as error:
        storage.fail_request(request_id, f"{type(error).__name__}: {error}")
        raise


def main() -> None:
    """Parse CLI input, initialize local services, and run one request."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="Question for the local agents")
    parser.add_argument(
        "--config", default="ci-cd/env/local.json", help="Path to local.json"
    )
    parser.add_argument("--session-id", help="Conversation memory session ID")
    parser.add_argument("--request-id", help="Idempotency key; generated if omitted")
    arguments = parser.parse_args()
    config = load_config(arguments.config)
    prompt = arguments.prompt or input("Prompt: ").strip()
    if not prompt:
        raise ValueError("prompt must not be empty")

    embeddings = OllamaEmbeddings(
        config.ollama, timeout_seconds=config.agent.request_timeout_seconds
    )
    global _RETRIEVER
    _RETRIEVER = HybridKnowledgeRetriever(
        config.opensearch,
        embeddings,
        config.ollama.embedding_dimensions,
        timeout_seconds=config.agent.request_timeout_seconds,
    )
    _RETRIEVER.ensure_index()

    session_id = arguments.session_id or config.agent.default_session_id
    request_id = arguments.request_id or str(uuid.uuid4())
    print(run_request(config, session_id, request_id, prompt))


if __name__ == "__main__":
    main()
