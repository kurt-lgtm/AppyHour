# Technology Stack — Matrix Commander Fulfillment Automation

**Project:** Matrix Commander (Shopify batch order editing, XLSX generation, fulfillment pipeline)
**Researched:** 2026-04-04
**Overall confidence:** HIGH (Shopify API docs verified, XLSX benchmarks from 2025 sources, pipeline patterns from official docs)

---

## Recommended Stack

### Shopify GraphQL Layer

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `requests` | >=2.32.5 (current) | GraphQL mutation execution | Already in stack. No additional SDK needed for orderEdit workflow. |
| Shopify GraphQL Admin API | 2025-10 or 2026-01 | Order edits, batch sync | Latest stable. 2026-01 raises concurrent bulk query limit to 5 vs 1. |

**Decision: Individual orderEdit mutations, NOT bulkOperationRunMutation.**

Shopify's `bulkOperationRunMutation` is for product/catalog updates via JSONL upload — it is asynchronous, processes over minutes, and does not support the `orderEditBegin` / `orderEditAddVariant` / `orderEditCommit` three-step workflow. The orderEdit API has no bulk equivalent. Each order requires its own begin→mutate→commit cycle.

For 2500 orders at 10 GraphQL points per mutation and a 50 points/second bucket (standard plan), the theoretical minimum is ~500 seconds raw. In practice the leaky bucket allows bursting. Target 1 edit per 200ms (5/sec) with exponential backoff on 429. At that rate 2500 orders completes in ~8-9 minutes — acceptable for a Saturday morning pipeline.

**Rate limit strategy (MEDIUM confidence — verify against your Shopify plan tier):**
- Standard plan: 50 points/sec (10 pts per mutation)
- Advanced plan: 100 points/sec
- Shopify Plus: 500 points/sec
- Implement leaky-bucket throttle in the ShopifyClient wrapper
- Inspect `X-GraphQL-Cost-Include-Fields` / `extensions.cost` in response to track actual spend
- Retry on 429 with `Retry-After` header, then exponential backoff

**Two-pass sequencing** (PR-CJAM first, then all others) is already a design constraint. Implement as two sequential `sync-shopify` calls with a gate between them — no async pipeline needed for this part.

---

### XLSX Generation

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `xlsxwriter` | >=3.2.0 | RMFG production sheet generation | Faster than openpyxl for write-only; streams to file, lower memory |
| `openpyxl` | >=3.1.5 (current) | Reading incoming XLSX (WeeklyProductionQuery imports) | Required for read+modify. Keep for import side only. |

**Decision: Split responsibilities. openpyxl for reading, xlsxwriter for writing.**

The RMFG production sheet is generated from scratch each week — it is a write-only operation. A 2025 benchmark showed openpyxl at 9 minutes vs xlsxwriter at 3 minutes for a 52-column / 200k-row report. Our output is smaller (~2500 rows, ~26 columns) but the pattern holds: xlsxwriter streams to disk and never loads the full workbook into memory, while openpyxl builds an in-memory object graph.

openpyxl must be retained for reading `AHB_WeeklyProductionQuery_*.xlsx` imports since xlsxwriter is write-only by design.

**What NOT to use:**
- `pandas` + openpyxl/xlsxwriter — adds a large dependency for no gain; the data is already structured as dicts/lists from Shopify/Recharge
- `xlwt` — Python 2 era, .xls format only, dead
- `XLSMerge` — niche library, no community, fragile dependency for production tooling

---

### Pipeline Orchestration

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Plain Python (dataclasses + functions) | stdlib | Step sequencing, state passing | Sufficient for a linear 6-step pipeline with one operator |
| `tenacity` | >=9.0 | Retry with exponential backoff + jitter | Best-in-class Python retry decorator; replaces hand-rolled retry loops |
| JSON file checkpoints | stdlib | Resume-after-crash safety | No external broker needed; each phase writes a checkpoint file |

**Decision: No Prefect, Celery, Airflow, or RQ.**

This pipeline runs once per week, triggered manually by one operator on a Windows desktop. It is a linear sequence: validate → swap → sync-shopify (pass 1) → sync-shopify (pass 2) → generate → finalize. There is no parallel fan-out, no distributed workers, and no scheduling requirement.

Prefect, Celery, and Airflow all require a broker/server process (Redis, RabbitMQ, or a Prefect agent) and add significant ops overhead. They solve distributed scheduling and worker pooling problems this project does not have.

The right pattern is a simple step-runner with checkpoint files:

```python
@dataclass(frozen=True)
class PipelineState:
    checkpoint: str          # "validated" | "swapped" | "pass1_complete" | ...
    order_count: int
    errors: tuple[str, ...]
    started_at: str
```

Each step reads the checkpoint file on startup, skips completed steps, and writes a new checkpoint on success. This gives crash-resume without any external dependency.

`tenacity` replaces the current hand-rolled retry loops in ShopifyClient. It handles exponential backoff with jitter, max attempts, and `retry_if_exception_type` predicates cleanly.

---

### Webhook Integration (React tool → Matrix Commander)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `flask` | >=3.1.2 (current) | Incoming webhook endpoint | Already in stack; matrix_commander_web/ already runs Flask |
| JSON file as idempotency store | stdlib | Deduplication of retried webhooks | No Redis needed; single desktop operator, low volume |

**Decision: No Redis. File-based idempotency is sufficient.**

Redis-based idempotency is the right call for high-volume SaaS webhook handlers. For a single-operator desktop tool receiving one webhook per weekly cycle, a JSON file keyed by `{event_type}:{timestamp}:{order_batch_hash}` is sufficient and avoids a new service dependency.

Pattern:
1. React tool POSTs `{ event: "allocation_complete", batch_id: "...", pass: 1|2 }`
2. Flask endpoint checks `idempotency_log.json` for `batch_id`
3. If new: write batch_id, launch pipeline step in daemon thread, return 202
4. If seen: return 200 immediately (idempotent)
5. React tool retries on non-2xx with 3 attempts + 5s backoff

**Shopify webhooks** (order/created, order/updated) are listed as not-yet-active in the codebase. When activated, add HMAC verification using `shopify-api-python`'s `WebhookValidator` or a manual `hmac.compare_digest` check against the `X-Shopify-Hmac-Sha256` header. This is a security requirement, not optional.

---

### Current Stack Items to Keep

| Item | Keep? | Reason |
|------|-------|--------|
| `requests` | Yes | Proven, all API calls work today |
| `openpyxl` | Yes | Required for reading weekly production queries |
| `pydantic` v2 | Yes | Validate API response shapes, pipeline state objects |
| `pytest` + `pytest-cov` | Yes | TDD workflow; 80% coverage target |
| `tenacity` | ADD | Replaces hand-rolled retry loops |
| `xlsxwriter` | ADD | Write-only XLSX generation for RMFG sheet |
| Prefect / Celery / Airflow | NO | Overkill for single-operator weekly pipeline |
| `shopify-api-python` (official SDK) | NO | Current `requests`-based ShopifyClient is simpler and sufficient; SDK adds OAuth complexity not needed for private app tokens |
| `pandas` | NO | No tabular analysis needed; adds 30MB+ dependency |
| `aiohttp` | NO | Async is not needed; synchronous requests with throttling is correct for this use case |

---

## Installation

```bash
# Add to pyproject.toml [project.dependencies] or [project.optional-dependencies]
tenacity>=9.0.0
xlsxwriter>=3.2.0

# openpyxl and requests already present
```

```bash
pip install tenacity xlsxwriter
```

---

## Shopify API Version Recommendation

Upgrade from `2024-10` to `2025-10` (or `2026-01` if stability confirmed).

- `2026-01` allows 5 concurrent bulk query operations vs 1 (not relevant for orderEdit, but useful for future reporting)
- Shopify deprecated REST for public apps Feb 2025, custom apps April 2025 — the codebase's existing GraphQL orderEdit path is already correct
- Verify the `orderEditBegin` / `orderEditAddVariant` / `orderEditSetQuantity` / `orderEditCommit` mutation signatures against `2025-10` before upgrading — field names are stable but always confirm

**Confidence:** HIGH for API structure. MEDIUM for exact point costs per mutation — verify with `Shopify-GraphQL-Cost-Debug: 1` header on first live run.

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| XLSX write | `xlsxwriter` | `openpyxl` (write path) | 3x slower for write-only; loads full workbook in memory |
| XLSX read | `openpyxl` | `xlsxwriter` | xlsxwriter is write-only, cannot read |
| Retry logic | `tenacity` | hand-rolled loops | tenacity handles jitter, max_delay, exception predicates correctly |
| Pipeline | plain Python | Prefect/Celery | No broker needed; one operator, one weekly run |
| Shopify GraphQL | `requests` wrapper | `shopify-api-python` | SDK adds session/OAuth management overhead; private app tokens don't need it |
| Bulk edits | individual orderEdit | `bulkOperationRunMutation` | Bulk mutations don't support orderEdit API; JSONL async approach incompatible with begin/commit workflow |

---

## Sources

- [Shopify GraphQL Admin API — Bulk Operations](https://shopify.dev/docs/api/usage/bulk-operations)
- [Shopify API Rate Limits](https://shopify.dev/docs/api/usage/limits)
- [orderEditBegin mutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/ordereditbegin)
- [orderEditCommit mutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/ordereditcommit)
- [bulkOperationRunMutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/bulkoperationrunmutation)
- [openpyxl Performance Docs](https://openpyxl.readthedocs.io/en/stable/performance.html)
- [openpyxl vs xlsxwriter — 200k row benchmark (2025)](https://mass-software-solutions.medium.com/optimizing-excel-report-generation-from-openpyxl-to-xlsmerge-processing-52-columns-200k-rows-5b5a03ecbcd4)
- [Webhook Idempotency Implementation Guide](https://hookdeck.com/webhooks/guides/implement-webhook-idempotency)
- [Shopify GraphQL Rate Limiting — Points Model](https://shopify.engineering/rate-limiting-graphql-apis-calculating-query-complexity)
- [shopify_python_api GitHub](https://github.com/Shopify/shopify_python_api)
