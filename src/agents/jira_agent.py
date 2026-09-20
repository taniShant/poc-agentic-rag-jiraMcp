"""Run the local Strands agent with Ollama, OpenSearch, MCP, and PostgreSQL."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters, stdio_client
from strands import Agent, tool
from strands.models.ollama import OllamaModel
from strands.tools.mcp import MCPClient

from src.common.config import LocalConfig, load_config
from src.memory.postgres import LocalStorage
from src.validations.critic_loop import CriticLoop, Critique, ResponseCritic
from src.vectorDb.embeddings import OllamaEmbeddings
from src.vectorDb.retrieval import HybridKnowledgeRetriever

SYSTEM_PROMPT = """
You are a local Jira investigation agent.

Use search_support_knowledge for historical tickets, Confluence runbooks,
knowledge-base articles, known issues, and vaguely described problems because
it combines OpenSearch keyword and vector retrieval.
Use Jira MCP tools when the user asks for current Jira Cloud facts or an exact
issue key. Clearly distinguish indexed snapshots from live Jira results. Never
invent ticket keys, status, descriptions, or URLs. This local agent is read-only;
do not claim to create, edit, transition, or delete Jira issues.
""".strip()

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


def _conversation_prompt(
    memory: list[dict[str, str]], current_prompt: str
) -> str:
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


def _invoke(
    config: LocalConfig,
    prompt: str,
    mcp_client: MCPClient | None,
) -> str:
    """Invoke Strands with Ollama and all currently available tools.

    Args:
        config: Complete local application configuration.
        prompt: Prompt including any selected conversation memory.
        mcp_client: Connected Jira MCP client, or ``None`` when Jira is disabled.

    Returns:
        Final agent response text.
    """
    model = OllamaModel(
        host=config.ollama.base_url,
        model_id=config.ollama.chat_model,
        temperature=config.ollama.temperature, # keep_alive="0", put this for  automatic unloading after each Strands request
        #Using keep_alive=0 saves memory but makes subsequent requests slower because Ollama must reload the model. A compromise such as "30s" is usually better for interactive testing.
    )
    tools: list[Any] = [search_support_knowledge]
    if mcp_client is not None:
        tools.extend(mcp_client.list_tools_sync())
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        callback_handler=None,
    )
    response = str(agent(prompt))
    if not config.validation.enabled:
        return response

    critic = ResponseCritic(
        config.ollama,
        config.validation,
        timeout_seconds=config.agent.request_timeout_seconds,
    )
    loop = CriticLoop(critic, config.validation.max_refinements)

    def revise(current: str, critique: Critique) -> str:
        """Ask the tool-enabled executor to address one critic report."""
        refinement_prompt = (
            f"{prompt}\n\n"
            "Revise the candidate response using the independent critic report. "
            "Re-check facts with tools when necessary. Preserve correct content, "
            "fix every listed issue, and never invent evidence.\n\n"
            f"CANDIDATE RESPONSE:\n{current}\n\n"
            f"CRITIC REPORT:\n{critique.refinement_context()}"
        )
        return str(agent(refinement_prompt))

    return loop.run(prompt, response, revise)


def run_request(
    config: LocalConfig,
    session_id: str,
    request_id: str,
    prompt: str,
) -> str:
    """Run one idempotent local agent request and persist its memory.

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
        if config.jira.enabled:
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
                response = _invoke(config, augmented_prompt, mcp_client)
        else:
            response = _invoke(config, augmented_prompt, None)

        storage.add_memory(session_id, "user", prompt)
        storage.add_memory(session_id, "assistant", response)
        storage.complete_request(request_id, response)
        return response
    except Exception as error:
        storage.fail_request(request_id, f"{type(error).__name__}: {error}")
        raise


def main() -> None:
    """Parse CLI input, initialize local services, and run one agent request."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="Question for the local agent")
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
    response = run_request(config, session_id, request_id, prompt)
    print(response)


if __name__ == "__main__":
    main()
