---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: Cut Order Consolidation
status: in_progress
stopped_at: Milestone initialized
last_updated: "2026-04-04T22:15:00Z"
last_activity: 2026-04-04 — Milestone v1.1 started
progress:
  total_phases: 3
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-04)

**Core value:** Single source of truth for demand calculation and cut order generation
**Current focus:** Phase 8 — XLSX v2 + Demand Fixes

## Current Position

Phase: 8 of 10 (XLSX v2 + Demand Fixes)
Plan: Not started (defining requirements)
Status: Defining requirements
Last activity: 2026-04-04 — Milestone v1.1 started

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

*Updated after each plan completion*

## Accumulated Context

### Decisions

- [v1.0]: Option 2 first (React + MC alongside) — prove logic before React absorbs it
- [v1.0]: Direct Shopify API over Matrixify — faster, no third-party dependency
- [v1.0]: Gift orders handled at matrix level only — Shopify blocks order edits on gift orders
- [v1.1]: Remove MONTHLY box exclusion — count all pickable items directly from charges
- [v1.1]: PR-CJAM-GEN is the only generic PR-CJAM; curation-specific variants created by Shopify post-charge
- [v1.1]: v2 XLSX as separate file (build_cut_order_xlsx_v2.py), keep v1 untouched
- [v1.1]: New milestone (not inserted phases) — consolidation is a different workflow from Matrix Commander

### Pending Todos

- Review v2 XLSX output for correctness and polish
- Verify demand numbers match Recharge CSV after MONTHLY fix

### Blockers/Concerns

- wheel_inventory in settings JSON is empty — bulk_weights populated at runtime from inventory CSV via extract_bulk_weights()
- inventory_demand_report.py dates are still hardcoded (parameterization needed in Phase 9)

## Session Continuity

Last session: 2026-04-04T22:15:00Z
Stopped at: Milestone v1.1 initialized — requirements and roadmap being created
Resume file: .planning/REQUIREMENTS.md
