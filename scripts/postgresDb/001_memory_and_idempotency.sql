CREATE TABLE IF NOT EXISTS agent_memory (
    memory_id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS agent_memory_session_created_idx
    ON agent_memory(session_id, created_at DESC, memory_id DESC);

CREATE TABLE IF NOT EXISTS idempotency_record (
    request_id VARCHAR(128) PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    response_text TEXT,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idempotency_record_session_idx
    ON idempotency_record(session_id, created_at DESC);
