# Local Strands + Ollama + OpenSearch + Jira MCP

This path runs independently of AWS. Every application setting is loaded from
`ci-cd/env/local.json`.

```text
                         Strands
                    /       |       \
                Ollama  OpenSearch   MCP (stdio)
                  LLM    BM25+k-NN      |
                                         Jira Cloud
                    
                         PostgreSQL
                         /         \
                     memory    idempotency
```

The local Jira MCP server is read-only. Creating the demonstration issues is a
separate, explicit command so an agent prompt cannot accidentally write to Jira.

## Components

- `src/agents/step_1_cli_entry.py`: CLI, MCP lifecycle, memory, and idempotency.
- `src/agents/step_2_orchestrator.py`: plan/execute/reflection coordination.
- `src/agents/step_3_planner.py`: tool-free Strands Planner.
- `src/agents/step_4_executor.py`: tool-enabled Strands Executor.
- `src/agents/step_5_critic.py`: independent tool-free Strands Critic.
- `src/vectorDb/ingestion.py`: Jira normalization, embedding, and OpenSearch
  document upserts.
- `src/vectorDb/retrieval.py`: weighted BM25/k-NN reciprocal-rank fusion.
- `src/vectorDb/opensearch_store.py`: index mapping and OpenSearch persistence.
- `src/vectorDb/embeddings.py`: local Ollama embedding client.
- `src/mcp/jira_server.py`: stdio MCP tools for live Jira JQL search and
  exact ticket retrieval.
- `src/mcp/rovo_mcp_oauth.py`: OAuth 2.1 browser consent and protected token cache
  for Atlassian's hosted Rovo MCP service.
- `src/mcp/rovo_mcp_client.py`: read-only Streamable HTTP client for Rovo MCP.
- `src/memory/postgres.py`: PostgreSQL conversation memory, request
  idempotency, and reflection audit records.
- `src/validations/step_6_critic_reflections.py`: hard-capped refinement, score plateau,
  identical-draft detection, and escalation.
- `scripts/postgresDb`: PostgreSQL schema migrations applied by bootstrap.
- `src/utility/seed_jira_tickets.py`: explicit idempotent Jira demo-ticket writer.
- `src/utility/bootstrap.py`: dependency checks plus PostgreSQL/OpenSearch setup.

## 1. Configure local.json

`ci-cd/env/local.json` has been created from
`ci-cd/env/local.example.json` and is ignored by Git.
Update it for your local services. In particular, verify the installed Ollama
model names and embedding dimensions.

The supplied OpenSearch settings already target the existing container:

```json
{
  "opensearch": {
    "url": "http://localhost:9200",
    "index_name": "local-support-knowledge"
  }
}
```

The running `opensearchproject/opensearch:latest` container was detected as
OpenSearch `3.8.0`, with the `opensearch-knn` plugin installed. The implementation
does not require an OpenSearch-hosted model or search pipeline; Ollama creates
raw vectors and the application combines BM25 and k-NN rankings.

`local.json` intentionally contains the local PostgreSQL password and, once
configured, a Jira API token. Do not commit it or reuse these credentials in a
shared environment.

## 2. Start PostgreSQL, OpenSearch, and Ollama

The included Compose file starts PostgreSQL and a single-node OpenSearch 3.8.0
development cluster together:

```bash
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml ps
```

If the separately created container named `opensearch` still exists, stop and
remove that container before starting this Compose project; otherwise port 9200
will already be occupied. The Compose-managed OpenSearch data is stored in the
`customer-agent-opensearch-data` volume. It does not automatically adopt data
from the old standalone container.

The local development cluster disables OpenSearch's security plugin because it
is bound only to localhost and `local.json` currently uses plain HTTP without
credentials. Do not expose this configuration outside the development machine.

Install/start Ollama and pull the two models named in `local.json`:

```bash
ollama serve
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

If a selected embedding model emits a different dimension, update
`ollama.embedding_dimensions` before bootstrapping. Changing the dimension after
the index exists requires using a new index name or deliberately recreating it.

## 3. Install and bootstrap

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.utility.bootstrap --config ci-cd/env/local.json
```

Bootstrap applies the memory/idempotency migration, verifies the Ollama models
and vector dimension, checks OpenSearch, and creates `local-support-knowledge` if it
does not exist. PostgreSQL migrations are loaded from `scripts/postgresDb`.

## 4. Connect Jira Cloud and create demo tickets

Set the Jira section in `local.json`:

```json
{
  "jiraMcp": {
    "enabled": true,
    "base_url": "https://your-company.atlassian.net",
    "email": "your-email@example.com",
    "api_token": "your-api-token",
    "project_key": "DEMO",
    "read_only": false,
    "sync_jql": "project = DEMO ORDER BY updated DESC",
    "sync_limit": 100
  }
}
```

For this local/ad-hoc integration, Jira authenticates with email plus an API
token. OAuth 2.0 should replace it for a distributed application.

Create the five labeled demonstration issues explicitly:

```bash
python -m src.utility.seed_jira_tickets --config ci-cd/env/local.json
```

The command searches for each unique label before creating an issue, so it can
be rerun safely. It changes Jira Cloud data. After seeding, set
`jiraMcp.read_only` back to `true`; the MCP tools themselves remain read-only in
either case.

Synchronize current Jira tickets into OpenSearch:

```bash
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source jira
```

Run synchronization again whenever the indexed snapshot should be refreshed.
Live exact-ticket questions can use MCP without waiting for a sync.

Populate all local Jira, Confluence, and known-issue samples:

```bash
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source samples \
  --skip-existing
```

The local fixture files are `golden_tickets.preview.json`,
`dummy_confluence_runbooks.json`, and `dummy_known_issues.json` under
`scripts/jira`. Source-specific refreshes use `sample-jira`,
`confluence`, or `known-issues`.

## 5. Run the agent

```bash
python -m src.agents.step_1_cli_entry \
  --config ci-cd/env/local.json \
  --session-id demo-session \
  --request-id request-001 \
  "Find tickets related to damaged laptop deliveries"
```

Use a new request ID for new input. Repeating the exact command returns the
stored PostgreSQL response without calling Ollama, OpenSearch, or Jira again.
Messages using the same session ID contribute recent conversation memory.

The Planner and Executor always run. Set `validation.enabled=true` to add the
Critic/refinement loop; leaving it `false` skips the extra model calls. After
adding or updating migrations, rerun bootstrap before invoking the agents.

## Troubleshooting

- `Connection refused` on port `9200`: run
  `docker compose -f docker-compose.local.yml ps` and confirm the OpenSearch
  health check has passed.
- Ollama `404`: pull the exact chat and embedding model names in `local.json`.
- Vector dimension mismatch: correct `embedding_dimensions` and select a new
  OpenSearch index name.
- Jira `401`: verify the account email and API token; Jira passwords are not
  supported.
- Jira MCP disabled: set `jiraMcp.enabled=true` after replacing all placeholders.
- PostgreSQL connection failure: start the Compose service or change the
  PostgreSQL section to an already-running local database.
