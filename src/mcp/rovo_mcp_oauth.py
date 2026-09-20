"""OAuth 2.1 support for the Atlassian Rovo MCP integration.

The MCP SDK performs authorization-server discovery, dynamic
client registration, PKCE, token exchange, and refresh-token handling.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class RovoFileTokenStorage:
    """Persist OAuth tokens and dynamic client registration with mode ``0600``."""

    def __init__(self, path: Path) -> None:
        """Initialize storage at a Git-ignored local path.

        Args:
            path: JSON file used for tokens and registered client metadata.
        """
        self._path = path

    def _read(self) -> dict[str, Any]:
        """Read and validate the token document when it exists."""
        if not self._path.exists():
            return {}
        value = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Rovo OAuth token store must contain a JSON object")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        """Atomically write token data with owner-only permissions."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            dir=self._path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2)
                stream.write("\n")
            os.replace(temporary_path, self._path)
            self._path.chmod(0o600)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    async def get_tokens(self) -> OAuthToken | None:
        """Return stored access and refresh tokens, if available."""
        value = self._read().get("tokens")
        return OAuthToken.model_validate(value) if value else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Persist updated OAuth tokens without discarding client metadata."""
        value = self._read()
        value["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(value)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Return dynamically registered OAuth client information."""
        value = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Persist dynamic client registration without discarding tokens."""
        value = self._read()
        value["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(value)


class RovoLocalOAuthFlow:
    """Open browser consent and receive the authorization code on loopback HTTP."""

    def __init__(self, redirect_uri: str, timeout_seconds: int = 300) -> None:
        """Validate and retain the loopback callback address.

        Args:
            redirect_uri: Registered loopback callback URL.
            timeout_seconds: Maximum time to wait for interactive consent.

        Raises:
            ValueError: If the callback is not an HTTP loopback URL with a port.
        """
        parsed = urlsplit(redirect_uri)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port is None
        ):
            raise ValueError("Rovo redirect URI must be HTTP loopback with a port")
        self._host = parsed.hostname
        self._port = parsed.port
        self._path = parsed.path or "/"
        self._timeout_seconds = timeout_seconds
        self._callback: asyncio.Future[tuple[str, str | None]] | None = None
        self._server: asyncio.Server | None = None

    async def redirect_handler(self, authorization_url: str) -> None:
        """Start the callback listener and open Atlassian consent in a browser."""
        loop = asyncio.get_running_loop()
        self._callback = loop.create_future()
        self._server = await asyncio.start_server(
            self._handle_callback,
            self._host,
            self._port,
        )
        opened = await asyncio.to_thread(
            webbrowser.open,
            authorization_url,
            new=1,
            autoraise=True,
        )
        if not opened:
            print(f"Open this OAuth URL in a browser:\n{authorization_url}")

    async def callback_handler(self) -> tuple[str, str | None]:
        """Wait for and return the OAuth authorization code and state."""
        if self._callback is None:
            raise RuntimeError("Rovo OAuth callback listener was not started")
        try:
            return await asyncio.wait_for(
                self._callback,
                timeout=self._timeout_seconds,
            )
        finally:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None

    async def _handle_callback(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Parse one loopback OAuth callback and complete the waiting future."""
        status = "200 OK"
        body = "Atlassian authorization completed. You may close this window."
        try:
            request_line = (await reader.readline()).decode("ascii", errors="replace")
            while await reader.readline() not in {b"\r\n", b"\n", b""}:
                pass
            parts = request_line.strip().split()
            if len(parts) < 2:
                raise ValueError("invalid OAuth callback request")
            callback_url = urlsplit(parts[1])
            if callback_url.path != self._path:
                raise ValueError("unexpected OAuth callback path")
            parameters = parse_qs(callback_url.query)
            if parameters.get("error"):
                description = parameters.get("error_description", parameters["error"])[0]
                raise PermissionError(f"Atlassian authorization failed: {description}")
            code = parameters.get("code", [""])[0]
            if not code:
                raise ValueError("OAuth callback did not contain an authorization code")
            state = parameters.get("state", [None])[0]
            if self._callback is not None and not self._callback.done():
                self._callback.set_result((code, state))
        except Exception as error:
            status = "400 Bad Request"
            body = "Atlassian authorization failed. Return to the terminal."
            if self._callback is not None and not self._callback.done():
                self._callback.set_exception(error)
        response = (
            f"HTTP/1.1 {status}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body.encode('utf-8'))}\r\n"
            "Connection: close\r\n\r\n"
            f"{body}"
        )
        writer.write(response.encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
