"""Step 8: execute one exact human-approved Jira action through Rovo MCP."""

from __future__ import annotations

import json
import uuid
from typing import Any

from strands.tools.mcp import MCPClient

from src.agents.contracts import ApprovedJiraAction, JiraAction, JiraMutationResult
from src.common.config import LocalConfig
from src.memory.postgres import LocalStorage
from src.mcp.rovo_mcp_client import create_rovo_mcp_client
from src.validations.step_7_human_approval import HumanApprovalValidator


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


def execute_approved_action(
    config: LocalConfig,
    approval: ApprovedJiraAction,
    storage: LocalStorage | None = None,
) -> JiraMutationResult:
    """Validate, claim, execute, and audit one approved Jira mutation.

    Args:
        config: Complete local runtime configuration.
        approval: Human approval constructed from an immutable stored proposal.
        storage: Optional repository override, primarily for composition tests.

    Returns:
        Successful Rovo MCP mutation result.

    Raises:
        RuntimeError: If Rovo or Critic validation is disabled or the tool fails.
        PermissionError: If proposal and approval safety checks fail.
        ValueError: If the approval or proposal has already been used.
    """
    if not config.rovo_mcp.enabled:
        raise RuntimeError("approved Jira writes require rovoMcp.enabled=true")
    if not config.validation.enabled:
        raise RuntimeError("approved Jira writes require validation.enabled=true")
    repository = storage or LocalStorage(config.postgres)
    stored = repository.get_jira_proposal(approval.request_id)
    HumanApprovalValidator().validate(
        approval,
        stored.proposal,
        stored.critic_verdict,
    )
    tool_name = WriterAgent.ACTION_TO_TOOL[approval.action]
    repository.claim_jira_approval(approval, tool_name)
    try:
        writer_client = create_rovo_mcp_client(
            config.rovo_mcp,
            role="writer",
            startup_timeout_seconds=config.mcp.startup_timeout_seconds,
            request_timeout_seconds=config.agent.request_timeout_seconds,
        )
        with writer_client:
            result = WriterAgent(
                writer_client,
                config.rovo_mcp.tools_for_role("writer"),
            ).execute(approval)
        repository.complete_jira_approval(result)
        return result
    except Exception as error:
        repository.fail_jira_approval(
            approval.approval_id,
            f"{type(error).__name__}: {error}",
        )
        raise
