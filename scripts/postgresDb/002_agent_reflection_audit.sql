CREATE TABLE IF NOT EXISTS agent_reflection_audit (
    audit_id BIGSERIAL PRIMARY KEY,
    request_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    iteration INTEGER NOT NULL CHECK (iteration > 0),
    draft_hash CHAR(64) NOT NULL,
    composite_score NUMERIC(5, 3) NOT NULL,
    criterion_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
    verdict TEXT NOT NULL CHECK (
        verdict IN ('PASS', 'REFINE', 'ESCALATE', 'ESCALATE_PLATEAU', 'SKIPPED')
    ),
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (request_id, iteration)
);

CREATE INDEX IF NOT EXISTS idx_agent_reflection_request
    ON agent_reflection_audit (request_id, iteration);

CREATE INDEX IF NOT EXISTS idx_agent_reflection_session_created
    ON agent_reflection_audit (session_id, created_at DESC);
