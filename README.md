# Local Strands Multi-Agent Jira Assistant

This project runs locally without AWS. It uses four isolated agent roles with
Ollama, OpenSearch, PostgreSQL, and an optional Jira Cloud MCP connection.

```text
User
  |
  v
Planner  --->  Executor  --->  Critic  ---> proposal stored
                 ^              |                 |
                 |---- REFINE ---|                 v
                 |                         human approval file
       OpenSearch + reader MCP                    |
                                                  v
                                         deterministic Writer
                                                  |
                                             writer MCP

PostgreSQL: memory + idempotency + reflection + proposal/execution audit
```

- The **Planner** creates an evidence-gathering plan and has no tools.
- The **Executor** can search OpenSearch and, when enabled, call the read-only
  Jira MCP tools.
- The **Critic** independently scores the answer and requests bounded revisions.
- The **Writer** can call one of four explicit Rovo write tools only after a
  Critic PASS and a matching, unexpired, one-time human approval.
- PostgreSQL stores runtime state. Jira, Confluence, and known-issue documents
  and their vectors are stored in OpenSearch.

## Prerequisites

Install these tools before starting:

- Python 3.11 or 3.12
- Docker Desktop with Docker Compose
- Ollama
- At least 6 GB of available memory for Docker and Ollama together

Check the installations:

```bash
python --version
docker --version
docker compose version
ollama --version
```

All commands below are run from the project root:

```bash
cd /Users/shantanu/Downloads/CodeProjects/AGENTIC_AI_PROJECTS/POC-AGENTIC-MPC-JIRA/poc-agentic-rag-jiraMcp
```

## 1. Create the Python environment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Activate `.venv` again whenever a new terminal is opened:

```bash
source .venv/bin/activate
```

## 2. Create and configure `local.json`

Create the local configuration if it does not already exist:

```bash
cp ci-cd/env/local.example.json ci-cd/env/local.json
```

`ci-cd/env/local.json` is ignored by Git. Keep passwords and Jira API tokens
only in this file and never commit it.

For the supplied Docker Compose services, the important values are:

```json
{
  "ollama": {
    "base_url": "http://localhost:11434",
    "chat_model": "qwen3:8b",
    "embedding_model": "nomic-embed-text",
    "embedding_dimensions": 768
  },
  "opensearch": {
    "url": "http://localhost:9200",
    "index_name": "local-support-knowledge",
    "username": "",
    "password": "",
    "verify_tls": false
  },
  "postgres": {
    "host": "localhost",
    "port": 5432,
    "database": "agentic_local",
    "username": "agentic_local",
    "password": "change-me",
    "ssl": false
  },
  "jiraMcp": {
    "enabled": false,
    "read_only": true
  },
  "validation": {
    "enabled": false,
    "max_refinements": 2,
    "pass_score": 4.0,
    "min_individual_score": 3,
    "plateau_patience": 3,
    "improvement_epsilon": 0.01
  },
  "portal": {
    "admin_username": "admin",
    "admin_password": "replace-with-a-strong-local-password",
    "secret_key": "replace-with-a-random-secret-of-at-least-32-characters",
    "approval_ttl_minutes": 10,
    "port": 8080
  }
}
```

Leave Jira disabled for the first local run. Setting `validation.enabled` to
`false` runs Planner and Executor but skips the additional Critic calls. Set it
to `true` when the full three-agent reflection workflow is required.

## 3. Start PostgreSQL and OpenSearch

The Compose file starts both services and persists their data in named Docker
volumes:

```bash
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml ps
```

Wait until both services report `healthy`. Useful checks are:

```bash
docker exec customer-agent-postgres pg_isready -U agentic_local -d agentic_local
curl http://localhost:9200/_cluster/health
```

If port `9200` is already occupied by an older standalone OpenSearch container,
stop or remove that specific container before starting this Compose project.

OpenSearch security is disabled for this localhost-only development setup. Do
not expose ports `9200`, `9600`, or `5432` to an external network.

## 4. Start Ollama and download the models

In a separate terminal, start Ollama if it is not already running:

```bash
ollama serve
```

In another terminal, download the models configured in `local.json`:

```bash
ollama pull qwen3:8b
ollama pull nomic-embed-text
ollama list
```

The configured embedding dimension must match the output of the embedding
model. This project expects `nomic-embed-text` to produce 768 dimensions.

## 5. Apply PostgreSQL migrations and bootstrap services

PostgreSQL, OpenSearch, and Ollama must all be running before bootstrap:

```bash
source .venv/bin/activate
python -m src.utility.bootstrap --config ci-cd/env/local.json
```

Bootstrap reads every `*.sql` file in `scripts/postgresDb` in lexical order and
applies it idempotently:

1. `001_memory_and_idempotency.sql`
   - creates `agent_memory`
   - creates `idempotency_record`
2. `002_agent_reflection_audit.sql`
   - creates `agent_reflection_audit`
3. `003_jira_action_approval.sql`
   - creates `jira_action_proposal`
   - creates `jira_action_execution`

Bootstrap also verifies the Ollama models and embedding dimension, checks the
OpenSearch cluster, and creates the configured OpenSearch index when missing.
Rerun the same bootstrap command whenever a new migration is added.

Verify the PostgreSQL tables without installing `psql` on the Mac:

```bash
docker exec customer-agent-postgres \
  psql -U agentic_local -d agentic_local -c '\dt'

docker exec customer-agent-postgres \
  psql -U agentic_local -d agentic_local \
  -c 'SELECT COUNT(*) FROM agent_memory;'

docker exec customer-agent-postgres \
  psql -U agentic_local -d agentic_local \
  -c 'SELECT COUNT(*) FROM agent_reflection_audit;'

docker exec customer-agent-postgres \
  psql -U agentic_local -d agentic_local \
  -c 'SELECT * FROM jira_action_execution ORDER BY created_at DESC LIMIT 10;'
```

## 6. Populate OpenSearch

Load the bundled sample data: 100 historical Jira tickets, dummy Confluence
runbooks/KB content, and known issues.

```bash
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source samples \
  --confluence-file scripts/opensearchDb/dummy_confluence_runbooks.json \
  --skip-existing
```

The source files are:

- `scripts/jira/golden_tickets.preview.json`
- `scripts/opensearchDb/dummy_confluence_runbooks.json`
- `scripts/jira/dummy_known_issues.json`

The ingestion process normalizes each record, generates its Ollama embedding,
and upserts it into `local-support-knowledge`. Stable document IDs prevent
duplicates. Omit `--skip-existing` when existing documents should be refreshed.

Available source selectors are:

```text
jira | sample-jira | confluence | known-issues | samples | all
```

Check the indexed document count:

```bash
curl http://localhost:9200/local-support-knowledge/_count
```

## 7. Run the multi-agent workflow

```bash
python -m src.agents.step_1_cli_entry \
  --config ci-cd/env/local.json \
  --session-id demo-session \
  --request-id request-001 \
  "Find resolved tickets and runbooks related to password reset failures"
```

Use a new `request-id` for each new request. Reusing the same ID with the same
input returns the stored PostgreSQL response without rerunning Ollama or search.
Reusing an ID with different input is rejected.

When Critic validation is enabled, inspect its audit trail with:

```bash
docker exec customer-agent-postgres \
  psql -U agentic_local -d agentic_local \
  -c 'SELECT request_id, iteration, composite_score, verdict, issues FROM agent_reflection_audit ORDER BY created_at DESC LIMIT 20;'
```

## 8. Optional Jira Cloud MCP setup

Jira is not required for the local sample-data workflow. To query live Jira,
update the `jira` object in `local.json`:

```json
{
  "jiraMcp": {
    "enabled": true,
    "base_url": "https://your-company.atlassian.net",
    "email": "your-email@example.com",
    "api_token": "your-api-token",
    "project_key": "SCRUM",
    "read_only": true,
    "sync_jql": "project = SCRUM ORDER BY updated DESC",
    "sync_limit": 100
  }
}
```

Use an Atlassian API token, not the account password. The MCP server exposes
only these read operations to the Executor:

- `search_jira_tickets`
- `get_jira_ticket`

To index current Jira results into OpenSearch:

```bash
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source jira
```

Creating dummy Jira tickets is a separate operator action and is not part of
the agent workflow. Tickets created in Jira Cloud remain there after local
Docker teardown.

### Alternative: hosted Atlassian Rovo MCP with OAuth 2.1

The Rovo MCP files integrate with Atlassian's hosted service. This is
an alternative to the local `jira_server.py`; enable only one integration.

- `src/mcp/rovo_mcp_oauth.py`: OAuth 2.1 browser callback and secure token storage.
- `src/mcp/rovo_mcp_client.py`: Streamable HTTP transport and Strands MCP client.

The implementation follows Atlassian's official endpoint and OAuth guidance:

- <https://developer.atlassian.com/cloud/rovo-mcp/guides/getting-started/>
- <https://developer.atlassian.com/cloud/rovo-mcp/guides/configuring-oauth-2-1/>
- <https://developer.atlassian.com/cloud/rovo-mcp/guides/supported-tools/>

Configure `local.json` as follows and keep `jiraMcp.enabled=false`:

```json
{
  "jiraMcp": {
    "enabled": false
  },
  "rovoMcp": {
    "enabled": true,
    "server_url": "https://mcp.atlassian.com/v2/mcp",
    "redirect_uri": "http://127.0.0.1:8765/oauth/callback",
    "token_store": ".local/rovo_oauth_tokens.json",
    "oauth_timeout_seconds": 300,
    "scopes": "read:jira:agent-interface search:jira:agent-interface write:jira:agent-interface",
    "roles": {
      "reader": {
        "allowed_tools": [
          "atlassianUserInfo",
          "getAccessibleAtlassianResources",
          "getJiraIssue",
          "searchJiraIssuesUsingJql",
          "discover",
          "executeRead"
        ]
      },
      "writer": {
        "allowed_tools": [
          "createJiraIssue",
          "editJiraIssue",
          "transitionJiraIssue",
          "addOrEditJiraIssueComment"
        ]
      }
    }
  }
}
```

On the first agent run, the MCP SDK discovers Atlassian's authorization server,
registers the client dynamically, creates PKCE state, and opens browser consent.
After approval, Atlassian redirects to the loopback callback. Tokens and dynamic
client registration are saved under `.local/` with owner-only file permissions.
Later runs reuse or refresh the stored token.

The allowlists intentionally exclude `executeWrite`, `executeDestructive`, and
all delete operations. Reader tools are exposed only with `rovo_reader_`
prefixes. Writer tools are never given to the Planner, Executor, or Critic. Your
Atlassian administrator may also need to allow the callback/domain in the Rovo
MCP administration settings. Delete `.local/rovo_oauth_tokens.json` to force a
new authorization flow.

### Human-approved Jira write workflow

Set both `rovoMcp.enabled` and `validation.enabled` to `true`. First run an
investigation with an explicit request ID:

```bash
python -m src.agents.step_1_cli_entry \
  --config ci-cd/env/local.json \
  --session-id jira-write-demo \
  --request-id change-001 \
  "Investigate SCRUM-7 and propose a verified resolution comment"
```

When the Critic passes a draft containing a proposal, the command prints an
approval JSON template and stores the immutable proposal in PostgreSQL. Copy
that JSON to a local file, replace the approval ID, approver, and timestamps,
and leave the issue key, action, payload, payload hash, and request ID exactly
unchanged. Use a short expiration window, for example 10 minutes.

Execute the approved action in a separate command:

```bash
python -m src.agents.step_1_cli_entry \
  --config ci-cd/env/local.json \
  --approval-file /absolute/path/to/approval.json
```

The write is rejected unless the stored Critic verdict is `PASS`, the approval
is current and unused, and every approved action field exactly matches the
stored proposal. The Writer directly calls exactly one mapped Rovo tool with
the approved payload; no LLM rewrites the mutation. PostgreSQL records the
approver, timestamps, payload hash, tool, status, response, and any failure.

## 9. Run tests

The unit tests do not require live PostgreSQL, OpenSearch, Ollama, or Jira:

```bash
python -m unittest discover -s tests -v
```

## 10. Run the administrator portal

The Compose stack includes a Dockerized Flask portal at
<http://localhost:8080>. It reads Jira ticket rows from the configured
OpenSearch index and proposal/execution state from PostgreSQL.

Before starting it:

1. Set a strong `portal.admin_password` and random `portal.secret_key` in
   `ci-cd/env/local.json`.
2. Apply all PostgreSQL migrations, including `003_jira_action_approval.sql`.
3. Populate OpenSearch with sample tickets or synchronize live Jira tickets.
4. For live approvals, set `rovoMcp.enabled=true` and
   `validation.enabled=true`.

Start PostgreSQL, OpenSearch, and the portal together:

```bash
docker compose -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.local.yml ps
```

Open <http://localhost:8080> and sign in with the administrator credentials
from `local.json`.

The first portal screen shows ticket key, status, team, priority, update time,
and approval state. Select a ticket to open the second screen, which shows the
problem statement, agent or recorded resolution, exact immutable Jira payload,
Critic verdict, payload hash, and approval button.

The Approve button creates a short-lived approval using the signed-in username.
The browser submits only the proposal request ID; the server reloads the exact
issue, action, payload, and hash from PostgreSQL before using the restricted
Rovo Writer. Completed or processing proposals cannot be approved again.

### Rovo OAuth prerequisite for portal writes

Complete the initial Rovo OAuth browser consent once from the host CLI before
using the portal approval button. The portal container mounts the resulting
`.local/rovo_oauth_tokens.json` and can reuse or refresh it:

```bash
python -m src.agents.step_1_cli_entry \
  --config ci-cd/env/local.json \
  --request-id oauth-check-001 \
  "Read SCRUM-7 and summarize its current status"
```

If only the local sample dataset is required, leave Rovo disabled. Tickets are
still visible, but write approval controls display as disabled.

View portal logs:

```bash
docker compose -f docker-compose.local.yml logs -f portal
```

## Routine start and stop

Start the persisted PostgreSQL and OpenSearch services:

```bash
docker compose -f docker-compose.local.yml up -d
```

Stop the containers while preserving all PostgreSQL and OpenSearch data:

```bash
docker compose -f docker-compose.local.yml stop
```

Resume them later with:

```bash
docker compose -f docker-compose.local.yml start
```

If `ollama serve` is running in a foreground terminal, press `Ctrl+C` in that
terminal to stop it. If Ollama was installed as a Homebrew service, use:

```bash
brew services stop ollama
```

Stopping Ollama does not delete downloaded models.

## Teardown

### Remove containers but preserve data

This removes the Compose containers and network. Named volumes—and therefore
the PostgreSQL tables and OpenSearch index—are preserved:

```bash
docker compose -f docker-compose.local.yml down
```

Restart later with `up -d`; migrations and indexed records will still exist.

### Full local reset, including all database and vector data

The following command permanently deletes the Compose project's PostgreSQL and
OpenSearch volumes. It does not delete Jira Cloud tickets or Ollama models.

```bash
docker compose -f docker-compose.local.yml down -v
```

After a full reset, rebuild the local state in this order:

```bash
docker compose -f docker-compose.local.yml up -d
python -m src.utility.bootstrap --config ci-cd/env/local.json
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source samples \
  --confluence-file scripts/opensearchDb/dummy_confluence_runbooks.json \
  --skip-existing
```

To deliberately remove downloaded Ollama models as well:

```bash
ollama rm qwen3:8b
ollama rm nomic-embed-text
```

## Troubleshooting

- **`ModuleNotFoundError`**: activate `.venv` and rerun
  `python -m pip install -r requirements.txt`.
- **PostgreSQL connection timeout/refused**: check
  `docker compose -f docker-compose.local.yml ps` and container logs.
- **OpenSearch connection refused**: wait for the health check, then run
  `curl http://localhost:9200/_cluster/health`.
- **OpenSearch port already allocated**: find the old container with
  `docker ps` and stop that exact container.
- **Ollama `404`**: ensure the exact models from `local.json` appear in
  `ollama list`.
- **Embedding dimension mismatch**: correct `embedding_dimensions`; if the old
  index uses another dimension, select a new index name or perform a full reset.
- **Jira `401`**: use the Atlassian account email and an API token, not the
  account password.
- **Slow Critic calls**: set `validation.enabled=false` for Planner + Executor
  operation, or use a smaller Ollama chat model.

## Main source locations

- `src/agents/step_1_cli_entry.py`: CLI and service initialization
- `src/agents/step_2_orchestrator.py`: workflow coordination
- `src/agents/step_3_planner.py`: tool-free Planner
- `src/agents/step_4_executor.py`: tool-enabled Executor
- `src/agents/step_5_critic.py`: independent Critic agent
- `src/validations/step_6_critic_reflections.py`: bounded Critic/Executor reflection controls
- `src/validations/step_7_human_approval.py`: strict approval gate
- `src/agents/step_8_writer.py`: deterministic, allowlisted Writer role
- `src/ui_portal/app.py`: authenticated portal routes and approval action
- `src/ui_portal/repository.py`: OpenSearch ticket and PostgreSQL approval views
- `src/ui_portal/templates`: login, ticket queue, and ticket detail UI
- `src/ui_portal/Dockerfile`: portal container image
- `src/vectorDb/ingestion.py`: OpenSearch input pipeline
- `src/vectorDb/retrieval.py`: hybrid retrieval
- `src/mcp/jira_server.py`: read-only Jira MCP server
- `src/memory/postgres.py`: memory, idempotency, reflection, and write audit persistence
- `scripts/postgresDb`: ordered PostgreSQL migrations
