"""Step 8: execute one exact human-approved Jira action through Rovo MCP."""

from __future__ import annotations

import json
import uuid
from typing import Any

from strands.tools.mcp import MCPClient

from src.agents.contracts import ApprovedJiraAction, JiraAction, JiraMutationResult


class WriterAgent:
    """Deterministic Writer role that cannot reinterpret an approved payload."""

    ACTION_TO_TOOL = {
        JiraAction.CREATE_ISSUE: "createJiraIssue",
        JiraAction.EDIT_ISSUE: "editJiraIssue",
        JiraAction.ADD_COMMENT: "addOrEditJiraIssueComment",
        JiraAction.TRANSITION_ISSUE: "transitionJiraIssue",
    }

    def __init__(self, mcp_client: MCPClient, allowed_tools: tuple[str, ...]) -> None:
        """Store the connected writer client and its explicit safe allowlist."""
        self._mcp_client = mcp_client
        self._allowed_tools = frozenset(allowed_tools)

    def execute(self, approval: ApprovedJiraAction) -> JiraMutationResult:
        """Call exactly one mapped Rovo tool with the approved payload unchanged."""
        tool_name = self.ACTION_TO_TOOL[approval.action]
        if tool_name not in self._allowed_tools:
            raise PermissionError(f"Writer role is not allowed to call {tool_name}")
        result = self._mcp_client.call_tool_sync(
            tool_use_id=str(uuid.uuid4()),
            name=tool_name,
            arguments=approval.payload,
        )
        response: dict[str, Any] = json.loads(
            json.dumps(dict(result), default=str, ensure_ascii=False)
        )
        if response.get("status") == "error" or response.get("isError") is True:
            raise RuntimeError(f"Rovo writer tool failed: {json.dumps(response)}")
        return JiraMutationResult(
            approval_id=approval.approval_id,
            issue_key=approval.issue_key,
            action=approval.action,
            tool_name=tool_name,
            response=response,
        )
