# PostgreSQL database scripts

This directory is the single source of truth for the local PostgreSQL schema.
Migration files are ordered lexically and must remain idempotent because
bootstrap may apply them more than once.

Current migration:

- `001_memory_and_idempotency.sql` creates `agent_memory` for recent
  conversation history and `idempotency_record` for exactly-once request
  processing.

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
```

 

SELECT * FROM agent_memory ORDER BY created_at DESC LIMIT 10;
SELECT * FROM idempotency_record ORDER BY created_at DESC LIMIT 10;


PostgreSQL does not store Jira tickets. Jira ticket snapshots and vectors belong in OpenSearch; PostgreSQL stores conversation memory and request-processing state.