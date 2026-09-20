CREATE TABLE IF NOT EXISTS jira_action_proposal (
    request_id VARCHAR(128) PRIMARY KEY
        REFERENCES idempotency_record(request_id),
    session_id VARCHAR(128) NOT NULL,
    issue_key VARCHAR(128) NOT NULL,
    action VARCHAR(32) NOT NULL CHECK (
        action IN ('create_issue', 'edit_issue', 'add_comment', 'transition_issue')
    ),
    payload JSONB NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    rationale TEXT NOT NULL,
    critic_verdict VARCHAR(32) NOT NULL CHECK (
        critic_verdict IN ('PASS', 'REFINE', 'ESCALATE', 'ESCALATE_PLATEAU', 'SKIPPED')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jira_action_execution (
    approval_id VARCHAR(128) PRIMARY KEY,
    request_id VARCHAR(128) NOT NULL
        REFERENCES jira_action_proposal(request_id),
    issue_key VARCHAR(128) NOT NULL,
    action VARCHAR(32) NOT NULL CHECK (
        action IN ('create_issue', 'edit_issue', 'add_comment', 'transition_issue')
    ),
    payload JSONB NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    approved_by VARCHAR(256) NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (
        status IN ('processing', 'completed', 'failed')
    ),
    tool_name VARCHAR(128) NOT NULL,
    response_json JSONB,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS jira_action_execution_active_request_idx
    ON jira_action_execution(request_id)
    WHERE status IN ('processing', 'completed');

CREATE INDEX IF NOT EXISTS jira_action_execution_created_idx
    ON jira_action_execution(created_at DESC);
