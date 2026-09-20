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

- `src/agents/jira_agent.py`: Strands agent backed by the local Ollama model.
- `src/vectorDb/ingestion.py`: Jira normalization, embedding, and OpenSearch
  document upserts.
- `src/vectorDb/retrieval.py`: weighted BM25/k-NN reciprocal-rank fusion.
- `src/vectorDb/opensearch_store.py`: index mapping and OpenSearch persistence.
- `src/vectorDb/embeddings.py`: local Ollama embedding client.
- `src/mcp/jira_server.py`: stdio MCP tools for live Jira JQL search and
  exact ticket retrieval.
- `src/memory/postgres.py`: PostgreSQL conversation memory and request
  idempotency.
- `src/validations/critic_loop.py`: bounded independent review and refinement.
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
  "jira": {
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
`jira.read_only` back to `true`; the MCP tools themselves remain read-only in
either case.

Synchronize current Jira tickets into OpenSearch:

```bash
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source jira
```

Run synchronization again whenever the indexed snapshot should be refreshed.
Live exact-ticket questions can use MCP without waiting for a sync.

Populate the vector database without Jira Cloud by loading the local golden
Jira records, dummy Confluence pages, and known issues:

```bash
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source samples \
  --skip-existing
```

The sample source loads:

- `scripts/jira/golden_tickets.preview.json`
- `scripts/jira/dummy_confluence_runbooks.json`
- `scripts/jira/dummy_known_issues.json`

Use `--source confluence`, `--source known-issues`, or
`--source sample-jira` to refresh only one local source. Document IDs are
namespaced and upserted, so rerunning ingestion updates records without
creating duplicates.

## 5. Run the agent

```bash
python -m src.agents.jira_agent \
  --config ci-cd/env/local.json \
  --session-id demo-session \
  --request-id request-001 \
  "Find tickets related to damaged laptop deliveries"
```

Use a new request ID for new input. Repeating the exact command returns the
stored PostgreSQL response without calling Ollama, OpenSearch, or Jira again.
Messages using the same session ID contribute recent conversation memory.

## Troubleshooting

- `Connection refused` on port `9200`: run
  `docker compose -f docker-compose.local.yml ps` and confirm the OpenSearch
  health check has passed.
- Ollama `404`: pull the exact chat and embedding model names in `local.json`.
- Vector dimension mismatch: correct `embedding_dimensions` and select a new
  OpenSearch index name.
- Jira `401`: verify the account email and API token; Jira passwords are not
  supported.
- Jira MCP disabled: set `jira.enabled=true` after replacing all placeholders.
- PostgreSQL connection failure: start the Compose service or change the
  PostgreSQL section to an already-running local database.
