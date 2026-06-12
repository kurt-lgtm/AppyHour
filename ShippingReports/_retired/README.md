# RETIRED — dead parallel ingest pipeline (M1 coldchain refactor, 2026-06-11)

These scripts built/enriched `output/shipments.db` — a deprecated build artifact that diverged
from the canonical DB. Retired after verifying **0 dead-only tracking rows** (every tracking in the
dead DB exists in canonical) and repointing all readers.

- Canonical DB: `%APPDATA%/AppyHour/shipping.db` (`appyhour_lib/paths.py::db_path()`)
- Sole importer: `GelPackCalculator/auto_import.py` (+ thin downloaders)
- Delivery enrichment: `backfill_sync.py` / `daily_shipping_sync.py` → `delivery_status`
- Wallet share: `build_wallet_share.py` (repointed to canonical)
- Veho INVOICE parser still lives in `../parsers/veho.py` (imported by auto_import) — parsers/ is NOT retired.
- Audit trail: `_outputs/reports/2026-06-11-codex-ingest-audit.md` · contract:
  `AppyHour/.claude/plans/2026-06-11-ORCHESTRATION-claude-codex-coldchain.md`

Do not run these. If you think you need one, the capability has a canonical home above.
