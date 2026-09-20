Jira Ticket Automation: Business Use Case & Architecture
Business Use Case
IT Service Management: Automated Ticket Triage & Resolution for a SaaS Enterprise

A mid-size SaaS company receives 3,000+ support and IT tickets per month in Jira Service Management. Tier-1 engineers spend 40% of their time on repetitive triage—reading tickets, searching Confluence runbooks, checking historical resolutions, and routing or resolving issues. Resolution time averages 4.2 hours, with 30% of tickets misrouted or delayed due to missing context.

The automation goal: Build an AI agent that ingests each incoming Jira ticket, retrieves relevant knowledge (historical tickets, Confluence runbooks, known-issue databases) via OpenSearch RAG, plans a resolution or routing strategy, and refines its answer through up to 8 iterative critique loops before writing back to Jira. The agent uses Atlassian's Rovo MCP server to interact with Jira natively, respecting existing permissions and security controls .

Expected outcomes (based on similar implementations):

Ticket resolution time reduced by ~47% (from 4.2 to 2.2 hours) 

Misrouted tickets reduced through semantic understanding of ticket context

Tier-1 engineers freed for complex, high-value work

Consistent, auditable resolution recommendations

Architecture Overview
The system follows a Plan-and-Execute agentic pattern with a Reflection Loop for answer refinement . A Planner agent decomposes the ticket into retrieval and action steps; an Executor carries out those steps; a Critic agent evaluates the draft answer and loops back for refinement (max 8 iterations) until quality thresholds are met or the cap is reached.

 ┌─────────────────────────────────────────────────────────────────────┐
│                        JIRA CLOUD (Atlassian)                        │
│  ┌──────────────┐    ┌──────────────────────────────────────────┐   │
│  │  Webhook /   │    │  Rovo MCP Server (mcp.atlassian.com)     │   │
│  │  Polling     │◄──►│  - OAuth 2.1 / API Token                 │   │
│  └──────┬───────┘    │  - Tool allowlisting (admin-controlled)  │   │
│         │            │  - Respects user permissions             │   │
│         │            └──────────────────────────────────────────┘   │
└─────────┼──────────────────────────────────────────────────────────┘
          │ Ticket Event
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATION LAYER (AWS)                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              Agent Orchestrator (LangGraph)                  │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────┐  │   │
│  │  │ PLANNER │──│EXECUTOR │──│ CRITIC  │──│ LOOP CONTROLLER │  │   │
│  │  │ Agent   │  │ Agent   │  │ Agent   │  │ (max 8 cycles)  │  │   │
│  │  └────┬────┘  └────┬────┘  └────┬────┘  └─────────────────┘  │   │
│  │       │            │            │                            │   │
│  │       ▼            ▼            ▼                            │   │
│  │  ┌─────────────────────────────────────────────────────┐     │   │
│  │  │              MEMORY SYSTEM                          │     │   │
│  │  │  ┌─────────────┐  ┌─────────────┐  ┌────────────┐   │     │   │
│  │  │  │ Working     │  │ Episodic    │  │ Semantic   │   │     │   │
│  │  │  │ Memory      │  │ Memory      │  │ Memory     │   │     │   │
│  │  │  │ (Session)   │  │ (Recent     │  │ (Long-term │   │     │   │
│  │  │  │             │  │  history)   │  │  facts)    │   │     │   │
│  │  │  └─────────────┘  └─────────────┘  └────────────┘   │     │   │
│  │  └─────────────────────────────────────────────────────┘     │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    KNOWLEDGE & RETRIEVAL LAYER                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Amazon OpenSearch Serverless (Vector Store)                 │   │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  │   │
│  │  │ Historical     │  │ Confluence     │  │ Known-issue    │  │   │
│  │  │ Tickets        │  │ Runbooks       │  │ Database       │  │   │
│  │  └────────────────┘  └────────────────┘  └────────────────┘  │   │
│  │                                                              │   │
│  │  FGAC (Fine-Grained Access Control)                          │   │
│  │  - Role-based index access                                   │   │
│  │  - Document-level filtering                                  │   │
│  │  - JWT-based tenant isolation (if multi-tenant)              │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘

Component Deep Dive
1. MCP Integration Layer (Atlassian Rovo)
The Atlassian Rovo MCP Server provides a standardized, secure bridge between AI agents and Jira Cloud . Key capabilities:

Aspect	Implementation
Connection	OAuth 2.1 (recommended) or API token for headless automation 
Tool Scope	Admin-controlled allowlisting—only enable jira_search, jira_create_issue, jira_add_comment, jira_transition_issue 
Permissions	Actions respect the authenticated user's existing Jira permissions—no privilege escalation 
IP Allowlisting	Organization-level IP allowlists still apply; requests must originate from approved IPs 
Security controls specific to MCP:

Domain allowlisting: Only approve trusted AI tool domains (e.g., your internal agent's domain) 

API token governance: If using API tokens for non-interactive automation, disable if not needed; tokens are governed by IP allowlists, not domain lists 

Explicit user consent: MCP spec requires hosts to obtain consent before tool invocation; for automation, document this as an approved service account policy 

2. Agent Orchestration (LangGraph)
The agent follows a Plan-and-Execute with Reflection pattern :

Planner Agent:

Decomposes the incoming ticket into a structured plan: which knowledge sources to query, what to search for, what Jira fields to update

Uses a reasoning model (e.g., Claude 3.7 Sonnet via Bedrock) for complex decomposition 

Output: Ordered list of retrieval/action steps

Executor Agent:

Carries out each step: performs vector searches, queries Jira via MCP, drafts the resolution or routing recommendation

May use a lighter model (e.g., Nova Lite) for straightforward retrieval steps 

Critic Agent (Reflection Loop):

Evaluates the Executor's draft against quality criteria: relevance to ticket, completeness, accuracy vs. retrieved context, alignment with runbook procedures

If quality score < threshold AND iteration < 8: sends specific feedback to Executor for refinement

If max iterations reached: escalates to human with the best draft and a flag indicating low confidence

Loop Control:

Hard cap: 8 refinement cycles 

Loop detection: Track action hashes to detect repetition; inject a "try different approach" prompt if stuck 

Progress monitoring: If no improvement in critic score over 3 cycles, break early and escalate

3. Memory System
Three-tier memory architecture :

Tier	Storage	Purpose	Retention
Working Memory	In-memory / Redis	Current ticket context, active plan, recent agent actions	Session duration
Episodic Memory	OpenSearch (vector)	Recent ticket resolutions, patterns from past 90 days	Rolling window
Semantic Memory	OpenSearch (vector)	Curated runbooks, known-issue entries, resolution playbooks	Permanent
Memory writes are controlled: Only human-approved resolutions enter semantic memory; agent-generated drafts are logged to episodic memory with a "pending review" flag.

4. RAG Layer (OpenSearch)
Data sources indexed:

Historical Jira tickets (title, description, comments, resolution, labels)

Confluence runbooks and KB articles

Known-issue databases (external or Confluence-hosted)

Vector search configuration:

Embedding model: Amazon Titan Embeddings or similar via Bedrock

Index: OpenSearch Serverless k-NN vectors 

Hybrid search: Combine vector similarity with keyword filters (project, issue type, component) for precision

Security in the RAG layer:

Encryption at rest: AWS KMS customer-managed keys for OpenSearch data 

Access control: OpenSearch FGAC roles restrict which indexes each agent role can query 

Input validation: Scan ingested documents for PII (Amazon Comprehend) and malware (Amazon S3 Malware Protection) before indexing 

Prompt injection defense: Validate and sanitize retrieved content before injecting into agent prompts; use Bedrock Guardrails to block malicious instructions embedded in documents 

5. Security Controls Summary
Following OWASP's framework for Agentic AI, controls map to three root causes of Excessive Agency :

Root Cause	Control	Implementation
Excessive Functionality	Tool allowlisting	Only enable MCP tools the agent actually needs: search, create issue, add comment, transition. Disable delete, assign, attachment tools. 
Excessive Permissions	Least-privilege service account	Agent's Jira service account has scoped project access; cannot modify other projects or admin settings. OpenSearch FGAC restricts to read-only on knowledge indexes. 
Excessive Autonomy	Human-in-the-loop gate	Agent drafts resolution/recommendation but requires human approval before: closing tickets, transitioning to "Resolved", or applying changes outside the ticket. Only comments and routing suggestions are auto-applied. 
Additional controls:

IP allowlisting: Restrict MCP server access to known corporate/VPC IP ranges 

Audit logging: Every MCP tool call, RAG query, and agent decision logged to CloudWatch with correlation IDs for traceability

Guardrails: Bedrock Guardrails for content filtering and PII detection on agent outputs before Jira writeback 

Data security policies: Atlassian Data Security Policies can block MCP access to sensitive content classifications even if the user has access 

Technology Stack Summary
Layer	Recommended Technology
MCP Server	Atlassian Rovo MCP (cloud-hosted) 
Agent Framework	LangGraph (Plan-Execute + Reflection) 
LLM	Amazon Bedrock: Claude 3.7 Sonnet (reasoning) + Nova Lite (fast tasks) 
Vector Store	Amazon OpenSearch Serverless (k-NN) 
Embeddings	Amazon Titan Embeddings via Bedrock
Memory	OpenSearch (episodic/semantic) + Redis (working) 
Orchestration	AWS Step Functions or Lambda-based agent runner
Guardrails	Amazon Bedrock Guardrails + custom prompt validation
Key Design Decisions
Why Plan-and-Execute over pure ReAct?
The ticket automation task has a defined structure (retrieve → analyze → recommend) and a known tool set (Jira MCP tools + OpenSearch query). Plan-and-Execute is more cost-effective here—one planning call vs. per-step reasoning—and the plan can be reviewed before execution begins .

Why 8 refinement loops?
The reflection loop improves answer quality for complex tickets where initial retrieval may miss context. The 8-cycle cap balances quality improvement against latency and cost. For simple tickets (e.g., password reset), the Critic will approve on cycle 1-2; complex infrastructure issues may use 5-7 cycles .

Why human-in-the-loop for destructive actions?
OWASP's guidance is clear: high-impact actions (closing tickets, changing status to Resolved) should not be fully autonomous. The agent recommends; the human confirms. This preserves accountability and prevents silent failures .

Cost consideration: Based on a similar AWS guidance, processing ~10,000 requests/day with OpenSearch Serverless, Bedrock, and Lambda costs approximately $510/month in US East (N. Virginia) . Your actual cost depends on ticket volume, retrieval frequency, and LLM token usage.

Go into more details here . Not code . Evaluates the Executor's draft against quality criteria: relevance to ticket, completeness, accuracy vs. retrieved context, alignment with runbook procedures

If quality score < threshold AND iteration < 8: sends specific feedback to Executor for refinement
Deep Dive: The Critic Agent & Refinement Loop
This is the heart of your system's quality control. Without it, the Executor produces a single-pass answer that may be incomplete, hallucinated, or misaligned with procedure. The Critic is what turns a "good enough" draft into a "production-ready" resolution. Here is exactly how it should work.

1. The Critic's Role in Plain Terms
The Critic is a separate LLM call (or a panel of calls) that receives:

The original Jira ticket (title, description, comments, metadata)

The retrieved context (documents, historical tickets, runbook excerpts)

The Executor's draft answer (resolution steps, routing recommendation, or comment)

The plan that was executed

The iteration number and prior feedback history

It does not try to solve the ticket itself. It only judges the draft against four criteria and produces a structured verdict. This separation matters: if the same agent both writes and judges, it tends to defend its own work. A separate Critic with a different prompt (and optionally a different model) gives an independent assessment.

2. The Four Quality Criteria — Expanded
Each criterion is scored on a 1–5 scale (1 = unacceptable, 5 = excellent) with a written justification. The Critic must cite specific evidence from the draft or retrieved context for every score. This prevents vague "looks fine" verdicts.

Criterion A: Relevance to the Ticket
What it measures: Does the draft actually address the specific problem the user described, or has it drifted into generic advice?

What the Critic looks for:

Does the draft restate and correctly interpret the user's core issue?

Are the recommended steps applicable to the exact product, version, environment, and error mentioned?

Does it ignore any part of a multi-part ticket (e.g., user reports two separate symptoms)?

Does it answer a different question than the one asked?

Scoring anchors:

Score	Meaning
5	Directly addresses every element of the ticket; no irrelevant content
3	Addresses the main issue but misses a secondary symptom or edge case
1	Generic or off-topic; would not help the user
Example feedback: "The draft recommends restarting the service, but the ticket specifies the service is on a read-only replica where restarts are prohibited by policy. This step is irrelevant and potentially harmful. Re-read the environment details in the ticket."

Criterion B: Completeness
What it measures: Does the draft cover everything needed for the user (or the next engineer) to act, without gaps?

What the Critic looks for:

Are all prerequisites stated (permissions, access, tools needed)?

Are there missing steps between "start" and "resolved"?

Does it include verification steps ("confirm X before proceeding")?

Does it include rollback or escalation guidance if the fix fails?

If routing, does it include the correct assignment group, priority, and rationale?

Scoring anchors:

Score	Meaning
5	Complete end-to-end; a competent engineer could execute without asking questions
3	Covers the main path but omits verification or rollback
1	Critical steps missing; execution would fail or cause harm
Example feedback: "The draft says to update the DNS record but does not specify the TTL value, the propagation wait time, or how to verify the change took effect. Retrieve the DNS change runbook and include those steps."

Criterion C: Accuracy vs. Retrieved Context
What it measures: Is every factual claim in the draft supported by the retrieved documents, or has the Executor hallucinated, invented details, or contradicted the source material?

What the Critic looks for:

Every command, URL, config value, error code, and version number — can it be traced to a retrieved document?

Does the draft contradict anything in the retrieved context?

Are citations present (e.g., "per runbook KB-1042")?

If the retrieved context was insufficient, did the Executor fabricate rather than flag the gap?

This is the most important criterion for trust. A draft that scores 5 on relevance and completeness but 1 on accuracy is dangerous — it looks right but will fail or cause damage.

Scoring anchors:

Score	Meaning
5	Every claim traceable to retrieved context; contradictions flagged
3	Mostly accurate but one unsupported detail or minor contradiction
1	Hallucinated commands, wrong values, or contradicts runbook
Example feedback: "The draft states the API rate limit is 1000 requests/minute. The retrieved runbook KB-2087 states 500 requests/minute for this tier. Correct the value and cite KB-2087. Also, the draft invents a 'reset cache' CLI command that does not appear in any retrieved document — remove it or retrieve the correct command."

Anti-hallucination technique: The Critic should be instructed to flag any sentence containing a specific value, command, or URL that it cannot find verbatim or semantically in the retrieved context. This forces the Executor to either cite or remove unsupported claims.

Criterion D: Alignment with Runbook Procedures
What it measures: Does the draft follow the organization's documented process, or has it improvised a shortcut, skipped an approval gate, or violated a policy?

What the Critic looks for:

Does the draft follow the steps in the matched runbook in the correct order?

Are required approvals or change-management gates included?

Does it respect constraints (e.g., "no changes during business hours," "requires DBA approval")?

Does it use the correct escalation path if the issue is out of scope?

Does it avoid prohibited actions (e.g., deleting logs, bypassing security controls)?

Scoring anchors:

Score	Meaning
5	Fully compliant with the governing runbook; all gates respected
3	Follows the spirit but skips a minor documentation step
1	Violates policy, skips approval, or contradicts the runbook
Example feedback: "The runbook KB-1042 requires a change ticket (CHG) before modifying production database settings. The draft proceeds directly to the ALTER statement without mentioning the CHG requirement. Insert the change-management gate and link the correct CHG template."

3. The Composite Quality Score
Each criterion is scored 1–5. The Critic then computes a weighted composite score:

Criterion	Weight	Rationale
Accuracy vs. context	40%	Wrong information is worse than missing information
Alignment with runbook	25%	Policy compliance is non-negotiable in ITSM
Completeness	20%	Gaps cause rework but are recoverable
Relevance	15%	Drift is annoying but detectable by the reader
Threshold: The draft passes when the weighted composite ≥ 4.0 AND no individual criterion scores below 3. This prevents a draft from passing on strong accuracy alone while being dangerously incomplete or off-topic.

If either condition fails, the Critic triggers refinement.

4. The Refinement Decision Logic
text
IF composite_score >= 4.0 AND min_individual_score >= 3:
    → PASS. Send draft to human-in-the-loop gate.
ELSE IF iteration >= 8:
    → ESCALATE. Flag low confidence, attach best draft + critic history, route to human.
ELSE IF no_improvement_for_3_cycles:
    → ESCALATE EARLY. The agent is stuck; more loops won't help.
ELSE:
    → REFINE. Send structured feedback to Executor.
Why the "no improvement for 3 cycles" early exit? Without it, the agent can burn all 8 iterations making lateral moves — rephrasing without improving. Tracking the composite score per cycle and breaking when it plateaus saves cost and latency. This is a form of progress monitoring, which is a known pattern for making iterative refinement loops robust against infinite or unproductive cycles.

5. What "Specific Feedback" Actually Looks Like
The single biggest failure mode of reflection loops is vague feedback. If the Critic says "make it better" or "be more accurate," the Executor will produce a near-identical draft. The feedback must be actionable, specific, and referenced to evidence.

Bad feedback (do not do this):
"The answer is incomplete and could be more accurate. Please improve."

Why it fails: The Executor has no idea what to change. It will rephrase the same content and score the same.

Good feedback (structured, specific, cited):
json
{
  "verdict": "REFINE",
  "iteration": 3,
  "composite_score": 2.85,
  "criterion_scores": {
    "relevance": 4,
    "completeness": 2,
    "accuracy": 2,
    "runbook_alignment": 3
  },
  "issues": [
    {
      "criterion": "accuracy",
      "severity": "critical",
      "location": "Step 2: 'Run `svc-restart --force` on the primary node'",
      "problem": "The --force flag does not appear in any retrieved document. KB-1042 explicitly warns against --force on primary nodes due to data loss risk.",
      "evidence": "KB-1042, section 'Restart Procedures', states: 'Never use --force on primary nodes. Use graceful restart only.'",
      "required_fix": "Remove --force. Replace with the graceful restart command from KB-1042 Step 4."
    },
    {
      "criterion": "completeness",
      "severity": "major",
      "location": "Missing step between Step 3 and Step 4",
      "problem": "Draft jumps from 'stop replication' to 'apply schema change' without verifying replication has fully stopped. Runbook requires a confirmation step.",
      "evidence": "KB-1042, Step 5: 'Verify replication lag is zero before proceeding.'",
      "required_fix": "Insert verification step with the exact command from KB-1042 Step 5."
    },
    {
      "criterion": "runbook_alignment",
      "severity": "major",
      "location": "Entire draft",
      "problem": "No change-management gate mentioned. Production DB changes require a linked CHG ticket per policy.",
      "evidence": "Runbook KB-1042, Prerequisites: 'A valid CHG ticket must be linked before execution.'",
      "required_fix": "Add prerequisite: 'Ensure CHG ticket is approved and linked.'"
    }
  ],
  "preserve": [
    "The initial diagnosis of the replication lag root cause is correct and well-reasoned.",
    "The escalation path if replication does not resume is accurate."
  ],
  "next_action": "Revise draft addressing the 3 issues above. Do not change the diagnosis or escalation sections."
}
Why this format works
Location tells the Executor exactly where the problem is.

Evidence grounds the critique in retrieved context — the Executor cannot argue with the runbook.

Required fix gives a concrete instruction, not a direction.

Preserve prevents the Executor from rewriting correct sections and introducing new errors — a common failure mode where refinement degrades previously good content.

Severity lets the Executor prioritize: fix critical accuracy issues first.

6. The Refinement Prompt Given to the Executor
The Executor receives the original ticket, the retrieved context, its prior draft, and the Critic's structured feedback. Its instruction is narrow:

"You previously produced the draft below. A separate reviewer identified the specific issues listed. Revise the draft to resolve every issue marked 'critical' or 'major.' Do not modify sections listed under 'preserve.' Do not introduce new claims that are not supported by the retrieved context. If you cannot resolve an issue because the retrieved context is insufficient, state that explicitly rather than guessing."

This last sentence is crucial: it gives the Executor permission to admit a gap rather than hallucinate a fix. Without it, the Executor will invent content to satisfy the Critic.

7. Loop History and Memory
Each iteration is logged with:

Iteration number

Draft hash (to detect identical re-submissions)

Composite and per-criterion scores

Critic feedback

Timestamp and token cost

This history serves three purposes:

Plateau detection — compare scores across iterations.

Audit trail — regulators and reviewers can see exactly why the final answer was chosen.

Learning — patterns of recurring critic feedback (e.g., "always forgets CHG gate") can be fed back into the Planner or runbook training.

The working memory holds the current draft + feedback. Episodic memory stores the full loop history for this ticket. If the same issue type recurs, the Planner can retrieve prior successful refinement patterns.

8. Human-in-the-Loop at the End
Regardless of whether the draft passes on iteration 1 or iteration 8:

Passed drafts go to a human reviewer who sees the draft, the critic scores, and the cited evidence. One-click approve → Jira writeback.

Escalated drafts (max iterations or plateau) go to a human with a "low confidence" flag, the best draft, and the full critic history so the human understands what the agent struggled with.

The agent never auto-closes a ticket. It can auto-add a comment (clearly labeled as AI-generated) and auto-route, but resolution and closure require human approval. This is the excessive-agency control: the agent's autonomy is bounded by the writeback scope.

9. Failure Modes to Design Against
Failure Mode	Mitigation
Critic is too lenient (passes bad drafts)	Calibrate with a golden dataset of known-good and known-bad drafts; require evidence citations for every score
Critic is too harsh (never passes)	Cap at 8 iterations; early-exit on plateau; track pass rate and tune threshold
Executor ignores feedback	Include feedback verbatim in the refinement prompt; verify in next cycle that cited issues were addressed
Executor degrades good content	"Preserve" list in feedback; diff-based check that preserved sections are unchanged
Loop oscillates (A→B→A)	Hash draft content; if a draft repeats, force escalation
Hallucination survives refinement	Accuracy weighted at 40%; unsupported claims must be cited or removed; Bedrock Guardrails as a final filter
10. Summary: The Loop in One Paragraph
The Planner decomposes the ticket. The Executor drafts a resolution using retrieved context. The Critic independently scores the draft on relevance, completeness, accuracy, and runbook alignment — with written evidence for every score. If the weighted composite is below 4.0 or any criterion falls below 3, the Critic emits structured, cited, actionable feedback naming exact locations and required fixes. The Executor revises, preserving sections the Critic marked as correct. This repeats up to 8 times, with early exit if scores plateau. The final draft — whether passed or escalated — goes to a human for approval before any Jira writeback. Every iteration is logged for audit and learning.

Now implement them as methods in 1 py file
Jira Ticket Automation — Single-File Implementation
Below is a complete, runnable Python file that implements the architecture described: MCP integration, OpenSearch RAG, three-tier memory, Planner/Executor/Critic agents, and the 8-cycle refinement loop.

Note: The MCP and OpenSearch calls are stubbed with clear # TODO markers because they require live credentials and network access. The agent logic, loop control, scoring, feedback structure, and memory are fully functional and can be tested with mocked data.

python
"""
jira_ticket_automation.py

Single-file implementation of a Jira ticket automation system using:
- Atlassian Rovo MCP for Jira interaction (stubbed client)
- Amazon OpenSearch for RAG retrieval (stubbed client)
- Three-tier memory (working / episodic / semantic)
- Planner / Executor / Critic agents
- Reflection loop with max 8 refinements

Run: python jira_ticket_automation.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("jira-automation")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    max_refinements: int = 8
    composite_threshold: float = 4.0
    min_individual_score: int = 3
    plateau_patience: int = 3          # cycles without improvement before early exit
    score_scale_max: int = 5
    criterion_weights: dict = field(default_factory=lambda: {
        "accuracy": 0.40,
        "runbook_alignment": 0.25,
        "completeness": 0.20,
        "relevance": 0.15,
    })
    # Jira / MCP
    jira_base_url: str = "https://your-domain.atlassian.net"
    mcp_server_url: str = "https://mcp.atlassian.com/v1/sse"
    jira_project_key: str = "SUPPORT"
    # OpenSearch
    opensearch_endpoint: str = "https://your-collection.aoss.amazonaws.com"
    opensearch_index_tickets: str = "historical-tickets"
    opensearch_index_runbooks: str = "confluence-runbooks"
    opensearch_index_known_issues: str = "known-issues"


# ---------------------------------------------------------------------------
# Enums and value objects
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    PASS = "PASS"
    REFINE = "REFINE"
    ESCALATE = "ESCALATE"
    ESCALATE_PLATEAU = "ESCALATE_PLATEAU"


class Severity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


@dataclass
class JiraTicket:
    key: str
    summary: str
    description: str
    issue_type: str
    priority: str
    components: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    environment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        blob = f"{self.key}|{self.summary}|{self.description}"
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class RetrievedDoc:
    doc_id: str
    source: str          # "ticket" | "runbook" | "known_issue"
    title: str
    content: str
    score: float = 0.0


@dataclass
class Draft:
    body: str
    citations: list[str] = field(default_factory=list)
    routing: Optional[str] = None
    priority: Optional[str] = None
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = hashlib.sha256(self.body.encode()).hexdigest()[:16]


@dataclass
class CritiqueIssue:
    criterion: str
    severity: Severity
    location: str
    problem: str
    evidence: str
    required_fix: str


@dataclass
class Critique:
    verdict: Verdict
    iteration: int
    composite_score: float
    criterion_scores: dict
    issues: list[CritiqueIssue] = field(default_factory=list)
    preserve: list[str] = field(default_factory=list)
    next_action: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "verdict": self.verdict.value,
            "iteration": self.iteration,
            "composite_score": round(self.composite_score, 2),
            "criterion_scores": self.criterion_scores,
            "issues": [
                {**asdict(i), "severity": i.severity.value} for i in self.issues
            ],
            "preserve": self.preserve,
            "next_action": self.next_action,
        }, indent=2)


@dataclass
class LoopRecord:
    iteration: int
    draft_hash: str
    composite_score: float
    criterion_scores: dict
    timestamp: float
    verdict: Verdict


# ---------------------------------------------------------------------------
# Memory — three tiers
# ---------------------------------------------------------------------------

class WorkingMemory:
    """Session-scoped, in-memory. Holds current ticket, plan, drafts, critiques."""

    def __init__(self):
        self.ticket: Optional[JiraTicket] = None
        self.plan: list[str] = []
        self.retrieved: list[RetrievedDoc] = []
        self.drafts: list[Draft] = []
        self.critiques: list[Critique] = []
        self.history: list[LoopRecord] = []

    def reset(self):
        self.__init__()

    def current_draft(self) -> Optional[Draft]:
        return self.drafts[-1] if self.drafts else None

    def last_critique(self) -> Optional[Critique]:
        return self.critiques[-1] if self.critiques else None


class EpisodicMemory:
    """Recent-ticket history. In production this is OpenSearch-backed."""

    def __init__(self):
        self._records: dict[str, list[LoopRecord]] = {}

    def store(self, ticket_key: str, history: list[LoopRecord]):
        # TODO: bulk-index into OpenSearch index configured in Config
        self._records[ticket_key] = list(history)
        log.debug("Episodic memory stored for %s (%d cycles)", ticket_key, len(history))

    def retrieve(self, ticket_key: str) -> list[LoopRecord]:
        return self._records.get(ticket_key, [])


class SemanticMemory:
    """Long-term curated knowledge. OpenSearch-backed in production."""

    def __init__(self):
        self._facts: list[dict] = []

    def add_approved_resolution(self, ticket_key: str, resolution: str, tags: list[str]):
        # TODO: index into OpenSearch with embeddings
        self._facts.append({
            "ticket": ticket_key,
            "resolution": resolution,
            "tags": tags,
            "approved_at": time.time(),
        })

    def query(self, topic: str, top_k: int = 5) -> list[dict]:
        # Stub — in production this is a k-NN vector search
        return [f for f in self._facts if topic.lower() in json.dumps(f).lower()][:top_k]


# ---------------------------------------------------------------------------
# MCP Client — Atlassian Rovo (stubbed)
# ---------------------------------------------------------------------------

class MCPClient:
    """
    Wraps the Atlassian Rovo MCP server.
    Only allowlisted tools are exposed: jira_search, jira_create_issue,
    jira_add_comment, jira_transition_issue.
    """

    ALLOWED_TOOLS = {
        "jira_search",
        "jira_create_issue",
        "jira_add_comment",
        "jira_transition_issue",
    }

    def __init__(self, config: Config, service_account_token: str = "STUB"):
        self.config = config
        self.token = service_account_token
        self._call_log: list[dict] = []

    def _call(self, tool: str, payload: dict) -> dict:
        if tool not in self.ALLOWED_TOOLS:
            raise PermissionError(f"Tool '{tool}' not in allowlist")
        # TODO: POST to MCP server with OAuth 2.1 bearer or API token
        # Example: requests.post(f"{self.config.mcp_server_url}/tools/{tool}",
        #                        headers={"Authorization": f"Bearer {self.token}"},
        #                        json=payload)
        self._call_log.append({"tool": tool, "payload": payload, "ts": time.time()})
        log.info("MCP call: %s", tool)
        return {"status": "stubbed", "tool": tool}

    def add_comment(self, ticket_key: str, comment: str) -> dict:
        return self._call("jira_add_comment", {"issueKey": ticket_key, "body": comment})

    def transition_issue(self, ticket_key: str, transition_id: str) -> dict:
        return self._call("jira_transition_issue",
                          {"issueKey": ticket_key, "transitionId": transition_id})

    def search(self, jql: str, limit: int = 10) -> dict:
        return self._call("jira_search", {"jql": jql, "maxResults": limit})

    @property
    def call_log(self) -> list[dict]:
        return list(self._call_log)


# ---------------------------------------------------------------------------
# OpenSearch RAG client (stubbed)
# ---------------------------------------------------------------------------

class OpenSearchRAG:
    """Hybrid retrieval: vector similarity + keyword filter."""

    def __init__(self, config: Config):
        self.config = config
        self._fake_corpus = self._bootstrap_fake_corpus()

    def _bootstrap_fake_corpus(self) -> list[RetrievedDoc]:
        return [
            RetrievedDoc("KB-1042", "runbook", "Primary DB restart procedure",
                         "Never use --force on primary nodes. Use graceful restart only. "
                         "Verify replication lag is zero before proceeding. "
                         "A valid CHG ticket must be linked before execution."),
            RetrievedDoc("KB-2087", "runbook", "API rate limits",
                         "Standard tier: 500 requests/minute. Enterprise tier: 2000 requests/minute."),
            RetrievedDoc("TKT-8812", "ticket", "Replication lag on primary DB",
                         "Resolved by graceful restart. Root cause: long-running transaction."),
            RetrievedDoc("KI-0031", "known_issue", "DNS propagation delays",
                         "TTL 300s. Verify with dig +short. Allow 10 minutes for propagation."),
        ]

    def search(self, query: str, top_k: int = 5) -> list[RetrievedDoc]:
        # TODO: replace with OpenSearch hybrid query
        # body = {"query": {"bool": {"should": [
        #     {"knn": {"embedding": {"vector": embed(query), "k": top_k}}},
        #     {"match": {"content": query}}
        # ]}}}
        q = query.lower()
        scored = []
        for doc in self._fake_corpus:
            overlap = sum(1 for w in q.split() if w in doc.content.lower())
            scored.append(RetrievedDoc(doc.doc_id, doc.source, doc.title,
                                       doc.content, float(overlap)))
        scored.sort(key=lambda d: d.score, reverse=True)
        return scored[:top_k]


# ---------------------------------------------------------------------------
# LLM stub — swap for Bedrock / OpenAI / Anthropic
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Thin wrapper around an LLM. Replace `_invoke` with a real Bedrock
    Converse / Anthropic / OpenAI call.
    """

    def __init__(self, model_id: str = "stub-model"):
        self.model_id = model_id

    def _invoke(self, system: str, user: str, temperature: float = 0.2) -> str:
        # TODO: real LLM call
        return json.dumps({"stub": True, "system": system[:80], "user": user[:120]})

    def invoke_json(self, system: str, user: str) -> dict:
        raw = self._invoke(system, user)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class PlannerAgent:
    """Decomposes a ticket into an ordered retrieval/action plan."""

    SYSTEM = (
        "You are a planning agent for IT service management tickets. "
        "Break the ticket into an ordered list of retrieval and action steps. "
        "Return strict JSON: {\"plan\": [\"step1\", \"step2\", ...]}"
    )

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def plan(self, ticket: JiraTicket) -> list[str]:
        user = json.dumps({
            "summary": ticket.summary,
            "description": ticket.description,
            "components": ticket.components,
            "environment": ticket.environment,
        })
        result = self.llm.invoke_json(self.SYSTEM, user)
        plan = result.get("plan")
        if not plan:
            # Deterministic fallback
            plan = [
                "search runbooks for matching symptoms",
                "search historical tickets for similar resolutions",
                "search known-issue database",
                "draft resolution or routing recommendation",
            ]
        log.info("Planner produced %d steps", len(plan))
        return plan


class ExecutorAgent:
    """Produces or revises a draft resolution using retrieved context."""

    SYSTEM = (
        "You are an execution agent. Given a ticket and retrieved context, "
        "produce a resolution draft. Every specific value, command, or URL must "
        "be traceable to the retrieved context. Cite doc IDs inline like [KB-1042]. "
        "If context is insufficient, state the gap explicitly — do not invent. "
        "Return strict JSON: {\"body\": str, \"citations\": [str], "
        "\"routing\": str|null, \"priority\": str|null}"
    )

    REVISION_SYSTEM = (
        "You previously produced a draft. A reviewer identified specific issues. "
        "Revise the draft to resolve every issue marked 'critical' or 'major'. "
        "Do NOT modify sections listed under 'preserve'. "
        "Do NOT introduce new claims not supported by the retrieved context. "
        "If you cannot resolve an issue because context is insufficient, say so "
        "explicitly rather than guessing. "
        "Return strict JSON: {\"body\": str, \"citations\": [str], "
        "\"routing\": str|null, \"priority\": str|null}"
    )

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def draft(self, ticket: JiraTicket, context: list[RetrievedDoc]) -> Draft:
        user = json.dumps({
            "ticket": ticket.to_dict(),
            "context": [asdict(c) for c in context],
        })
        result = self.llm.invoke_json(self.SYSTEM, user)
        return self._to_draft(result)

    def revise(self, ticket: JiraTicket, context: list[RetrievedDoc],
               prior: Draft, critique: Critique) -> Draft:
        user = json.dumps({
            "ticket": ticket.to_dict(),
            "context": [asdict(c) for c in context],
            "prior_draft": {"body": prior.body, "citations": prior.citations},
            "critique": json.loads(critique.to_json()),
        })
        result = self.llm.invoke_json(self.REVISION_SYSTEM, user)
        return self._to_draft(result)

    @staticmethod
    def _to_draft(result: dict) -> Draft:
        return Draft(
            body=result.get("body", result.get("raw", "")),
            citations=result.get("citations", []) or [],
            routing=result.get("routing"),
            priority=result.get("priority"),
        )


class CriticAgent:
    """Independent judge. Scores a draft on four criteria with evidence."""

    SYSTEM = (
        "You are an independent reviewer of IT resolution drafts. "
        "Score on four criteria (1-5): relevance, completeness, accuracy, "
        "runbook_alignment. For EVERY score below 5, cite specific evidence "
        "from the retrieved context or the draft. Flag any sentence containing "
        "a specific value, command, or URL that you cannot find in the retrieved "
        "context as a hallucination. "
        "Return strict JSON: {\"criterion_scores\": {\"relevance\": int, "
        "\"completeness\": int, \"accuracy\": int, \"runbook_alignment\": int}, "
        "\"issues\": [{\"criterion\": str, \"severity\": \"critical|major|minor\", "
        "\"location\": str, \"problem\": str, \"evidence\": str, "
        "\"required_fix\": str}], "
        "\"preserve\": [str]}"
    )

    def __init__(self, llm: LLMClient, config: Config):
        self.llm = llm
        self.config = config

    def evaluate(self, ticket: JiraTicket, context: list[RetrievedDoc],
                 draft: Draft, iteration: int) -> Critique:
        user = json.dumps({
            "ticket": ticket.to_dict(),
            "context": [asdict(c) for c in context],
            "draft": {"body": draft.body, "citations": draft.citations},
            "iteration": iteration,
        })
        result = self.llm.invoke_json(self.SYSTEM, user)
        return self._build_critique(result, iteration)

    def _build_critique(self, result: dict, iteration: int) -> Critique:
        scores = result.get("criterion_scores", {})
        # Ensure all criteria present with defaults
        for c in self.config.criterion_weights:
            scores.setdefault(c, 3)

        composite = self._composite(scores)
        issues = [self._to_issue(i) for i in result.get("issues", [])]
        verdict = self._decide_verdict(scores, composite, iteration)

        return Critique(
            verdict=verdict,
            iteration=iteration,
            composite_score=composite,
            criterion_scores=scores,
            issues=issues,
            preserve=result.get("preserve", []),
            next_action=self._next_action_text(verdict),
        )

    def _composite(self, scores: dict) -> float:
        total = 0.0
        for criterion, weight in self.config.criterion_weights.items():
            total += scores.get(criterion, 0) * weight
        return round(total, 3)

    def _decide_verdict(self, scores: dict, composite: float,
                        iteration: int) -> Verdict:
        min_ok = all(
            scores.get(c, 0) >= self.config.min_individual_score
            for c in self.config.criterion_weights
        )
        if composite >= self.config.composite_threshold and min_ok:
            return Verdict.PASS
        if iteration >= self.config.max_refinements:
            return Verdict.ESCALATE
        return Verdict.REFINE

    @staticmethod
    def _to_issue(raw: dict) -> CritiqueIssue:
        sev = raw.get("severity", "minor").lower()
        try:
            severity = Severity(sev)
        except ValueError:
            severity = Severity.MINOR
        return CritiqueIssue(
            criterion=raw.get("criterion", "unknown"),
            severity=severity,
            location=raw.get("location", ""),
            problem=raw.get("problem", ""),
            evidence=raw.get("evidence", ""),
            required_fix=raw.get("required_fix", ""),
        )

    @staticmethod
    def _next_action_text(verdict: Verdict) -> str:
        return {
            Verdict.PASS: "Send to human approval gate.",
            Verdict.REFINE: "Revise draft addressing critical/major issues; preserve listed sections.",
            Verdict.ESCALATE: "Max iterations reached. Escalate to human with full history.",
            Verdict.ESCALATE_PLATEAU: "No score improvement. Escalate to human early.",
        }[verdict]


# ---------------------------------------------------------------------------
# Orchestrator — the refinement loop
# ---------------------------------------------------------------------------

class Orchestrator:
    """Runs the plan-execute-critique loop with plateau detection and audit."""

    def __init__(self, config: Config, planner: PlannerAgent,
                 executor: ExecutorAgent, critic: CriticAgent,
                 rag: OpenSearchRAG, mcp: MCPClient,
                 working: WorkingMemory, episodic: EpisodicMemory,
                 semantic: SemanticMemory):
        self.config = config
        self.planner = planner
        self.executor = executor
        self.critic = critic
        self.rag = rag
        self.mcp = mcp
        self.working = working
        self.episodic = episodic
        self.semantic = semantic

    # ----- public entry point ------------------------------------------------

    def process(self, ticket: JiraTicket) -> Critique:
        log.info("=== Processing ticket %s ===", ticket.key)
        self.working.reset()
        self.working.ticket = ticket

        # 1. Plan
        self.working.plan = self.planner.plan(ticket)

        # 2. Retrieve context per plan step
        self.working.retrieved = self._retrieve_context(ticket, self.working.plan)

        # 3. Initial draft
        draft = self.executor.draft(ticket, self.working.retrieved)
        self.working.drafts.append(draft)

        # 4. Refinement loop
        final_critique = self._refinement_loop(ticket)

        # 5. Persist episode
        self.episodic.store(ticket.key, self.working.history)

        # 6. Human-in-the-loop gate
        self._submit_for_approval(ticket, final_critique)
        return final_critique

    # ----- loop internals ----------------------------------------------------

    def _retrieve_context(self, ticket: JiraTicket,
                          plan: list[str]) -> list[RetrievedDoc]:
        seen: dict[str, RetrievedDoc] = {}
        for step in plan:
            query = f"{ticket.summary} {step}"
            for doc in self.rag.search(query, top_k=3):
                # Keep highest-scoring occurrence
                if doc.doc_id not in seen or doc.score > seen[doc.doc_id].score:
                    seen[doc.doc_id] = doc
        docs = sorted(seen.values(), key=lambda d: d.score, reverse=True)
        log.info("Retrieved %d unique docs", len(docs))
        return docs

    def _refinement_loop(self, ticket: JiraTicket) -> Critique:
        best_score = -1.0
        no_improvement_cycles = 0

        for iteration in range(1, self.config.max_refinements + 1):
            draft = self.working.current_draft()
            critique = self.critic.evaluate(
                ticket, self.working.retrieved, draft, iteration
            )
            self.working.critiques.append(critique)
            self.working.history.append(LoopRecord(
                iteration=iteration,
                draft_hash=draft.hash,
                composite_score=critique.composite_score,
                criterion_scores=critique.criterion_scores,
                timestamp=time.time(),
                verdict=critique.verdict,
            ))
            log.info("Iter %d | composite=%.2f | verdict=%s",
                     iteration, critique.composite_score, critique.verdict.value)

            if critique.verdict == Verdict.PASS:
                return critique
            if critique.verdict == Verdict.ESCALATE:
                return critique

            # Plateau detection
            if critique.composite_score > best_score + 0.01:
                best_score = critique.composite_score
                no_improvement_cycles = 0
            else:
                no_improvement_cycles += 1
                if no_improvement_cycles >= self.config.plateau_patience:
                    log.warning("Plateau detected after %d cycles. Early escalation.",
                                no_improvement_cycles)
                    critique.verdict = Verdict.ESCALATE_PLATEAU
                    critique.next_action = self.critic._next_action_text(
                        Verdict.ESCALATE_PLATEAU
                    )
                    return critique

            # Refine
            revised = self.executor.revise(
                ticket, self.working.retrieved, draft, critique
            )
            if revised.hash == draft.hash:
                log.warning("Executor produced identical draft. Escalating.")
                critique.verdict = Verdict.ESCALATE
                return critique
            self.working.drafts.append(revised)

        return self.working.last_critique()

    # ----- approval gate -----------------------------------------------------

    def _submit_for_approval(self, ticket: JiraTicket, critique: Critique):
        draft = self.working.current_draft()
        if critique.verdict == Verdict.PASS:
            header = "AI-generated resolution (pending human approval)"
        else:
            header = (f"AI-generated draft — {critique.verdict.value} "
                      f"(composite {critique.composite_score:.2f})")
        comment = f"{header}\n\n{draft.body}\n\n---\nCritic report:\n{critique.to_json()}"

        # Auto-comment is allowed; auto-transition is NOT.
        self.mcp.add_comment(ticket.key, comment)
        log.info("Draft submitted for human approval (comment added to %s)", ticket.key)

    # ----- post-approval -----------------------------------------------------

    def record_human_approval(self, ticket: JiraTicket, approved_resolution: str,
                              tags: list[str]):
        """Call after a human approves a draft, to promote it to semantic memory."""
        self.semantic.add_approved_resolution(ticket.key, approved_resolution, tags)


# ---------------------------------------------------------------------------
# Demo / self-test
# ---------------------------------------------------------------------------

def _demo():
    cfg = Config()

    llm_fast = LLMClient("nova-lite-stub")
    llm_reasoning = LLMClient("claude-sonnet-stub")

    planner = PlannerAgent(llm_reasoning)
    executor = ExecutorAgent(llm_reasoning)
    critic = CriticAgent(llm_reasoning, cfg)

    rag = OpenSearchRAG(cfg)
    mcp = MCPClient(cfg, service_account_token="demo-token")

    working = WorkingMemory()
    episodic = EpisodicMemory()
    semantic = SemanticMemory()

    orch = Orchestrator(cfg, planner, executor, critic, rag, mcp,
                        working, episodic, semantic)

    ticket = JiraTicket(
        key="SUPPORT-4412",
        summary="Primary database replication lag spiking to 45 minutes",
        description=("Replication lag on the primary DB has been growing for 2 hours. "
                     "Read replicas are serving stale data. Cannot restart during "
                     "business hours per change policy."),
        issue_type="Incident",
        priority="High",
        components=["database", "replication"],
        labels=["prod", "p1-candidate"],
        environment="prod-eu-west-1",
    )

    final = orch.process(ticket)
    print("\n=== FINAL CRITIQUE ===")
    print(final.to_json())
    print("\n=== MCP CALL LOG ===")
    print(json.dumps(mcp.call_log, indent=2))


if __name__ == "__main__":
    _demo()
What to replace for production
Stub	Replace with
MCPClient._call	Real HTTP call to mcp.atlassian.com with OAuth 2.1 bearer token
OpenSearchRAG.search	OpenSearch hybrid query (k-NN + BM25) with FGAC role
LLMClient._invoke	Amazon Bedrock converse API (Claude for reasoning, Nova for fast)
EpisodicMemory / SemanticMemory	OpenSearch indexes with embeddings
Critique.to_json logging	CloudWatch with correlation IDs
Key invariants enforced in code
Tool allowlist — MCPClient._call raises PermissionError for any tool outside ALLOWED_TOOLS.

Hard iteration cap — _refinement_loop uses range(1, max_refinements + 1).

Plateau detection — 3 cycles without >0.01 composite improvement triggers ESCALATE_PLATEAU.

Identical-draft detection — if the Executor's revision hash matches the prior draft, immediate escalation.

Composite + floor — CriticAgent._decide_verdict requires both composite >= 4.0 and every criterion >= 3.

Human-in-the-loop — _submit_for_approval only calls jira_add_comment. It never calls jira_transition_issue. Transitions require a separate human-triggered path.

Preserve list — passed through Critique.preserve into the Executor's revision prompt to prevent degradation of correct sections.

Audit trail — every cycle appended to LoopRecord with draft hash, scores, and timestamp.

