# Jira golden dataset seeder

`create_completed_golden_tickets.py` prepares 100 deterministic synthetic
service cases across 20 categories. Every case contains a request, business
justification, pre-change checks, approved resolution steps, validation evidence,
historical dates, and an idempotency label.

The script creates Jira issues, adds their canonical resolution comments,
discovers the project's workflow transitions, moves each issue into Jira's
`Done` status category, and verifies the result. It never stores the Atlassian
account password and never prints the API token.

## Important date behavior

The historical opened and completed dates are part of each synthetic issue's
description. Jira's system `created`, `updated`, and resolution timestamps record
the real time when this script runs; the standard Jira REST API does not backdate
those audit fields.

## 1. Preview without changing Jira

From the project root:

```bash
python scripts/jira/create_completed_golden_tickets.py \
  --export-json scripts/jira/golden_tickets.preview.json
```

Review the JSON before executing. The preview file contains no credentials.

## 2. Enable the controlled seed operation

In the Git-ignored `ci-cd/env/local.json`, temporarily set:

```json
"enabled": true,
"read_only": false
```

Confirm that `base_url`, `email`, `api_token`, and `project_key` are correct.
The API-token account requires Browse Projects, Create Issues, Add Comments,
and Transition Issues permissions in the project.

## 3. Create and complete the tickets

The SCRUM template normally uses the issue type `Task` and completed status
`Done`:

```bash
python scripts/jira/create_completed_golden_tickets.py \
  --execute \
  --confirm-project SCRUM \
  --issue-type Task \
  --done-status Done
```

If the workflow calls its completed status `Resolved` or `Closed`, replace the
`--done-status` value. Omit `--done-status` to select an available status whose
Jira status category is `Done`.

The operation is idempotent. Each issue carries a unique label such as
`golden-ticket-001`; rerunning the command reuses that issue, avoids duplicating
the canonical resolution comment, and ensures it is completed.

## 4. Return the application to read-only mode

Set this value immediately after successful seeding:

```json
"read_only": true
```

Verify the dataset in Jira with:

```jql
project = SCRUM
AND labels = local-agent-golden-data
AND statusCategory = Done
ORDER BY created ASC
```

Then synchronize the resolved cases into local OpenSearch:

```bash
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source jira
```

For an offline local demonstration, index the exported golden ticket records
together with the dummy Confluence runbooks and known issues:

```bash
python -m src.vectorDb.ingestion \
  --config ci-cd/env/local.json \
  --source samples
```
