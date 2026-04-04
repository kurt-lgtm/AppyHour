# Phase 2: Shopify Sync Battle-Test - Discussion Log (Assumptions Mode)

> **Audit trail only.** Do not use as input to planning, research, or execution agents.

**Date:** 2026-04-04
**Phase:** 02-Shopify Sync Battle-Test
**Mode:** assumptions (--auto)
**Areas analyzed:** Phase 1 Integration, Two-Pass Sequencing, Idempotency, Error-But-Applied, Partial Failure Recovery

## Assumptions Presented

### Phase 1 Integration
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| Replace ThreadPoolExecutor with sequential loop | Confident | rate_limiter.py not thread-safe, checkpoint_store.py not designed for concurrent writers |
| Wire all Phase 1 modules into cmd_sync | Confident | None imported yet despite being built |

### Two-Pass Sequencing
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| Single function with pass_number param, SKU prefix filtering | Likely | SKIP_PREFIXES at line 313, PipelineState has PASS1/PASS2 stages |

### Idempotency
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| Pre-commit read + commit_pending checkpoint flag | Likely | current_skus check at lines 1390-1407, PITFALLS.md on read-after-write gap |

### Error-But-Applied
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| commit_pending flag before orderEditCommit, verification read on error | Likely | PITFALLS.md Pitfall 1, no committed flag in SyncResult |

### Partial Failure Recovery
| Assumption | Confidence | Evidence |
|------------|-----------|----------|
| --retry-failed flag loading checkpoint, re-running failed orders only | Confident | PassProgress.failed list exists, ROADMAP criterion 3 |

## Auto-Resolved

- Two-Pass: auto-selected single function with pass_number param (recommended)
- Idempotency: auto-selected pre-commit read + commit_pending flag (safer option)
- Error-But-Applied: auto-selected commit_pending flag approach (recommended by PITFALLS.md)
