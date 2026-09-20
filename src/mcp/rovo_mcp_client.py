"""Strands MCP client for Atlassian's hosted Rovo MCP server."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)
from mcp.shared.auth import OAuthClientMetadata
from strands.tools.mcp import MCPClient

from src.common.config import RovoMcpConfig
from src.mcp.rovo_mcp_oauth import RovoFileTokenStorage, RovoLocalOAuthFlow


@asynccontextmanager
async def rovo_streamable_http_transport(
    config: RovoMcpConfig,
    timeout_seconds: int,
) -> AsyncIterator[tuple[object, object, object]]:
    """Connect to Atlassian Rovo MCP over OAuth-authenticated Streamable HTTP.

    Args:
        config: Rovo endpoint, OAuth, token-storage, and allowlist settings.
        timeout_seconds: Standard network request timeout.

    Yields:
        MCP read stream, write stream, and session-ID callback.
    """
    storage = RovoFileTokenStorage(config.token_store)
    flow = RovoLocalOAuthFlow(
        config.redirect_uri,
        timeout_seconds=config.oauth_timeout_seconds,
    )
    metadata = OAuthClientMetadata(
        redirect_uris=[config.redirect_uri],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=config.scopes,
        client_name="poc-agentic-mcp-local",
    )
    oauth = OAuthClientProvider(
        config.server_url,
        metadata,
        storage,
        redirect_handler=flow.redirect_handler,
        callback_handler=flow.callback_handler,
        timeout=float(config.oauth_timeout_seconds),
    )
    timeout = httpx.Timeout(
        timeout_seconds,
        read=max(timeout_seconds, config.oauth_timeout_seconds),
    )
    async with create_mcp_http_client(timeout=timeout, auth=oauth) as client:
        async with streamable_http_client(
            config.server_url,
            http_client=client,
        ) as streams:
            yield streams


def create_rovo_mcp_client(
    config: RovoMcpConfig,
    startup_timeout_seconds: int,
    request_timeout_seconds: int,
) -> MCPClient:
    """Create a read-only Strands client for Atlassian Rovo MCP.

    Args:
        config: Rovo OAuth and MCP settings from ``local.json``.
        startup_timeout_seconds: Normal MCP connection timeout.
        request_timeout_seconds: Network timeout for MCP requests.

    Returns:
        A Strands MCP client restricted to the configured read-only tools.
    """
    if not config.enabled:
        raise RuntimeError("Rovo MCP integration is disabled in local.json")
    effective_startup_timeout = max(
        startup_timeout_seconds,
        config.oauth_timeout_seconds + 30,
    )
    return MCPClient(
        lambda: rovo_streamable_http_transport(config, request_timeout_seconds),
        startup_timeout=effective_startup_timeout,
        tool_filters={"allowed": list(config.allowed_tools)},
        prefix="rovo",
    )
