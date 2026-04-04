# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-04)

**Core value:** Produce a correct, ready-to-email RMFG production sheet in under 15 minutes, including gift orders
**Current focus:** Phase 1 — Pipeline Foundation

## Current Position

Phase: 1 of 7 (Pipeline Foundation)
Plan: 0 of ? in current phase
Status: Ready to plan
Last activity: 2026-04-04 — Roadmap created, all 24 requirements mapped to 7 phases

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: -
- Trend: -

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Init]: Option 2 first (React + MC alongside) — prove logic before React absorbs it
- [Init]: Direct Shopify API over Matrixify — faster, no third-party dependency
- [Init]: Gift orders handled at matrix level only — Shopify blocks order edits on gift orders

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 2]: Exact GraphQL cost per orderEdit mutation sequence unknown — measure with Shopify-GraphQL-Cost-Debug:1 header on first live run before finalizing throttle params
- [Phase 2]: asyncio vs synchronous chunked requests conflict in research — resolve during Phase 2 planning with live 50-order test
- [Phase 2]: Shopify plan tier (Standard vs Plus) affects rate limit ceiling — verify before finalizing semaphore size

## Session Continuity

Last session: 2026-04-04
Stopped at: Roadmap created and written to disk. Ready to plan Phase 1.
Resume file: None
