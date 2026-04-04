# Phase 1: Pipeline Foundation - Discussion Log (Assumptions Mode)

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the analysis.

**Date:** 2026-04-04
**Phase:** 01-Pipeline Foundation
**Mode:** assumptions (--auto)
**Areas analyzed:** Checkpoint Store, Rate Limiter, Pipeline State Machine, Dry-Run Mode

## Assumptions Presented

### Checkpoint Store Design
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| JSON file at .pipeline/checkpoint.json with per-order granularity | Likely | matrix_commander_web/app.py lines 63-75 (in-memory STATE), ARCHITECTURE.md Pattern 4, research ruling out external broker |

### Rate Limiter Design
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| Synchronous leaky-bucket + tenacity, replacing sleep(0.1-0.2) | Likely | matrix_commander.py lines 1153, 1185, 1559 (bare sleeps), STACK.md tenacity recommendation, STACK.md rejecting aiohttp |

### Pipeline State Machine
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| Python dataclass with forward-only enum stages | Confident | matrix_commander_web/app.py lines 63-75 (mutable dict with no enforcement), ARCHITECTURE.md frozen dataclass pattern |

### Dry-Run Mode
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| Enforce at ShopifyClient layer, preserve existing cmd_sync logic | Confident | matrix_commander.py lines 1318, 1370-1398 (existing dry_run=True), app.py line 392 (web dry_run param) |

## Corrections Made

No corrections — all assumptions auto-confirmed.

## Auto-Resolved

- Checkpoint Store: auto-selected JSON with per-order granularity (recommended default)
- Rate Limiter: auto-selected synchronous leaky-bucket + tenacity (recommended, STACK.md rejects async)

## External Research

- tenacity version compatibility: one-command check, not a blocker
- Shopify plan tier bucket size: needs live test with debug header — deferred to Phase 2
- asyncio vs synchronous: resolved in favor of synchronous per STACK.md recommendation
