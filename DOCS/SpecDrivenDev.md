A mature Agentic SDD architecture

Describing complete system as:

                 BUSINESS REQUIREMENT
                         │
                         ▼
                  SPECIFICATION
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   Functional        Security        Governance
      Specs            Specs            Specs
        │                │                │
        └────────────────┼────────────────┘
                         ▼
                   Planning Agent
                         │
                         ▼
                    Coding Agent
                         │
                         ▼
                       CODE
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
       Testing        Security      Governance
        Agent          Review         Review
          │              │              │
          ▼              ▼              ▼
      Unit/E2E       SAST/SCA       Policy tests
      AI evals       Secrets        Compliance
          │              │              │
          └──────────────┼──────────────┘
                         ▼
                    QUALITY GATE
                         │
                  ┌──────┴──────┐
                PASS           FAIL
                  │              │
                  ▼              ▼
               Human        Coding Agent
               Review          fixes
                  │
                  ▼
               CI/CD
                  │
                  ▼
             PRODUCTION
                  │
                  ▼
           Runtime Governance

And notice that governance exists twice.

Development-time governance prevents insecure/non-compliant software getting deployed.

Runtime governance controls what the deployed agent can actually do.

For your Jira example, runtime controls include authorization, MCP tool allowlists, approval gates, idempotency, audit logs, content filtering and monitoring.


Then your development pipeline becomes:

Developer / Coding Agent
          │
          ▼
         Code
          │
          ▼
┌─────────────────────────────┐
│ CI/CD Quality Gates         │
│                             │
│ Unit tests                  │
│ Integration tests           │
│ Agent evaluations           │
│ SAST / dependency scanning  │
│ Secret scanning             │
│ IaC policy checks           │
│ Governance policy tests     │
└──────────────┬──────────────┘
               │
          PASS │ FAIL
               │
       ┌───────┴───────┐
       ▼               ▼
     Deploy           Block