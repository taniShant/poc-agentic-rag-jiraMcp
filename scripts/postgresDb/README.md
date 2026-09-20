# PostgreSQL database scripts

This directory is the single source of truth for the local PostgreSQL schema.
Migration files are ordered lexically and must remain idempotent because
bootstrap may apply them more than once.

Current migrations:

- `001_memory_and_idempotency.sql` creates `agent_memory` for recent
  conversation history and `idempotency_record` for exactly-once request
  processing.
- `002_agent_reflection_audit.sql` records every Critic cycle, including the
  draft hash, quality scores, verdict, and issues.
- `003_jira_action_approval.sql` stores immutable Jira write proposals and the
  one-time human approval/execution audit, including response or failure.

Apply the migrations together with the other local bootstrap checks:

```bash
python -m src.utility.bootstrap --config ci-cd/env/local.json
```

Inspect the resulting schema:

```bash
docker exec -it customer-agent-postgres \
  psql -U agentic_local -d agentic_local
```

Then run:

```sql
\dt
\d agent_memory
\d idempotency_record
\d agent_reflection_audit
\d jira_action_proposal
\d jira_action_execution
```

 

SELECT * FROM agent_memory ORDER BY created_at DESC LIMIT 10;
SELECT * FROM idempotency_record ORDER BY created_at DESC LIMIT 10;
SELECT * FROM agent_reflection_audit ORDER BY created_at DESC LIMIT 10;
SELECT * FROM jira_action_proposal ORDER BY created_at DESC LIMIT 10;
SELECT * FROM jira_action_execution ORDER BY created_at DESC LIMIT 10;


PostgreSQL does not store Jira tickets. Jira ticket snapshots and vectors belong
in OpenSearch; PostgreSQL stores conversation memory, request-processing state,
and the reflection audit trail.
