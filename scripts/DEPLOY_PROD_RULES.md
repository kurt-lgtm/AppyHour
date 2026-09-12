# DEPLOY_PROD_RULES — constraints SSOT for `scripts/deploy_prod.py`

> 🔴 **PRE-CHANGE GATE.** Read this before changing `deploy_prod.py`, before loosening any refusal
> in it, and before typing `--apply`. It is the single source of truth for what this tool refuses
> and why. Change the rules **here first**, in the same commit as the code.

## 🧭 North star

`C:\AppyHourProd\AppyHour` is the tree the Windows scheduled tasks execute. This tool exists so that
what runs there is **a deployment that runs**, not merely a set of files that copied. Every rule
below is subordinate to that: an artifact-level success (bytes match, hash matches, log row written)
that leaves prod unable to execute is a **failure of this tool**, not a partial win.

---

## 🔴 What this tool must never do (negatives first)

### N1. Never confuse a verified COPY with a runnable DEPLOYMENT

**The burn — 2026-09-02, three instances in ONE day (`_config/ENGINEERING_GOTCHAS.md` D1, ranked #1
of the unguarded classes).**

1. Between 09:50 and 10:25 six files were deployed to `C:\AppyHourProd\AppyHour`, including
   `ShippingReports/carrier_mix_pivot.py`, **without** `appyhour_lib/credentials.py`. Prod's copy of
   that provider was still the **2026-06-23** version, exposing only `get_shopify_credentials` /
   `get_shopify_auth` / `get_openweather_key`. Result: `ImportError: cannot import name
   'get_google_credentials'` on **every** run. **Nine more files** were held back by hand because
   they would have died identically. Unblocked by `a220576`.
2. `lib.canon` missing from a prod/archived ShipRouting tree → `ModuleNotFoundError` (2026-08-31).
3. `GoogleIntegration(None)` against a prod copy whose constructor still took `credentials_path:
   str` → `TypeError`.

An independent instance on **2026-07-27** ("the module shipped without its importer") makes this two
dated shapes, not one accident.

🔴 **Every one of those deploys passed this tool's own post-copy byte comparison.** Byte-identity
proves the artifact landed; it says nothing about whether the tree it landed in can import it. The
unit that must be consistent is the **import closure**, and nothing in a diff, a hash, a deploy log
row, or a reviewer's attention spans it.

**The invariant.** A file may not be deployed unless every module and symbol it imports resolves in
the **destination tree** — checked against that tree's actual files, never against dev and never
against the deploy log.

**Guard:** `preflight_imports()` runs before the first copy of every `--apply`, and prints on every
dry run. Refusal exit code **3** (distinct from **2**, the prod-newer refusal). Zero copies, zero log
rows, all-or-nothing.

### N2. Never let `--only` or a missing `--include-new` silently split a fix

`--include-new` makes new files **opt-in** and `--only` narrows the set — those two flags are exactly
how a consumer ships while its brand-new provider stays behind. Both are correct features (see N6),
so the preflight is what makes them safe. **The fix for a refusal is to widen the deploy so the
provider ships in the SAME run, never to work around the check.**

Verified live on 2026-09-07, first run against the real drift set: 40 files checked, one violation —
`ingest/slack_reship/sync.py` imports `ingest.slack_reship.matrix_history`, which exists in dev and
**not** in prod. A `--apply` without `--include-new` would have reproduced 09-02 exactly.

### N3. 🔴 Never import a module in order to check it

Much of this tree does real work at module scope: Slack posts, Google Sheet writes, Shopify order
edits, `shipping.db` opens, live API calls. **A deploy gate that fires a live write is worse than no
gate.** Resolution is therefore **AST + filesystem only** — the preflight parses source and walks
directories, and imports nothing from either tree, ever.

Consequences that are deliberate, not oversights:
- Facts that cannot be established without executing something are reported **UNCHECKED** and
  printed — never assumed fine, never silently dropped. That covers `from x import *`, dynamic
  `importlib.import_module`, and providers that define names through `globals()`/`setattr`/a module
  `__getattr__`.
- A provider whose source will not parse is **opaque**: symbol checks against it are skipped and
  reported. A *consumer* in the copy set that will not parse is a hard refusal (`UNPARSEABLE`) — it
  would die on import in prod.
- Never "solve" an UNCHECKED line by importing the module to look. Add the fact to this doc instead.

### N4. 🔴 Never let the editable install answer for prod

`appyhour_lib` is a **pip editable install mapped to the DEV tree** (gotcha D2). An in-process
`import appyhour_lib.credentials` resolves to dev and reports a **false PASS** on a prod tree that is
missing the symbol. Two structural defences, both required:

- **Path-anchored resolution, no import.** Modules resolve by walking the destination directories
  (the consumer's own dir, then the destination root) — dir-anchored, never a bare-basename match,
  which would resolve `utils` to any of the eight `utils.py` in the tree and invent a provider.
- **An isolated subprocess on a production-shaped path.** `python -E -s -B <this file>
  --preflight-child <job>`; the child rewrites `sys.path` to `[prod_root] + …` with every dev-tree
  and workspace entry removed, and returns the proof with its result: `sys.path[0]`, the count of
  dev entries, and whether `appyhour_lib` was ever imported. Live measurement 2026-09-07:
  `sys.path[0]=C:\AppyHourProd\AppyHour`, dev entries `0`, `appyhour_lib imported False`.

🔴 **`deploy_prod.py` must never import `automation_health` in child mode.** That module does
`sys.path.insert(0, <dev repo root>)` at import time and pulls `appyhour_lib` through the editable
install — it would put the dev tree on the very path this check exists to prove is prod-shaped. The
conditional import at the top of the file is load-bearing; do not "tidy" it into an unconditional one.

### N5. 🔴 Never refuse on breakage this deploy did not introduce

An alarm that fires on another session's pre-existing mess trains everyone to bypass it (gotcha A1 —
the prod-drift alarm is the worked example, 36 stale files paging daily and read by nobody). The
preflight grades the destination tree **BEFORE** and **AFTER** the copy set and blocks only on
violations that are **NEW**. Pre-existing violations are **printed, never dropped** — deleting them
would hide the day one becomes reachable.

### N6. Never add a force flag — to this refusal or the prod-newer one

Same doctrine as the prod-newer refusal that predates it: there is no `--force`, and
`--no-preflight` affects the **dry-run report only** and has no effect under `--apply`. If a
violation is wrong, fix the checker and its test; if it is right, ship the provider. An override
would restore exactly the capability the 09-02 outage used.

### N7. Never widen the scope of what the checker treats as first-party

A module is in scope **only if it resolves inside the DEV tree** — i.e. it is our code and must be
deployed. Stdlib and third-party (`gspread`, `openpyxl`, …) are deliberately out of scope: dev and
prod share one interpreter and one site-packages, so they are not a two-trees problem. Widening this
to "everything importable" would turn a quiet, actionable gate into the daily noise of N5.

### N8. Never let the resolver drift from rule 19's

`automation_health._resolve_module` / `_module_imports` (HEARTBEAT_RULES rule 19,
`check_prod_entry_points`) already own dir-anchored module resolution for this tree. The preflight
carries a second copy **only** because the child must not import that module (N4).
`tests/test_deploy_prod_import_preflight.py::ResolverParityWithRule19` pins the two together on a
fixture set. If you change one resolver, that test must be the thing that tells you.

---

### N9. 🔴 Never map GelPackCalculator through this tool (2026-09-12, plan R-35 phase 1)

`GelPackCalculator/` is its own git repo (kurt-lgtm/GelPackCalculator). Until 2026-09-12 it sat
NESTED inside the dev tree (gitignored, `.gitignore:46`) and `--only "GelPackCalculator/*"` copied it
into `C:\AppyHourProd\AppyHour\GelPackCalculator`, where 7 scheduled tasks run it (`run_carrier_sync.bat`,
`daily_shipping_sync.py` ×4, `sync_logon.py` ×2). The dev checkout now lives at
`Claude Projects/GelPackCalculator/` (resolved everywhere via `appyhour_lib.paths.gelpack_root()`), so
`classify()` — which walks the DEV tree — simply no longer sees it. Silence is the failure: a scoped
deploy that matches nothing would print "clean" and the operator would believe GelPackCalculator shipped.

- Any `--only` pattern naming `GelPackCalculator` is **REFUSED, exit 2**, with the Phase-2 pointer.
  No override — same shape as N6.
- **Phase 2 (Kurt-gated):** re-home prod to `C:\AppyHourProd\GelPackCalculator` + retarget the 7 tasks
  (admin, real profile). After that, GelPackCalculator deploys from its OWN repo (its own deploy step,
  not this file); until then the prod copy is frozen at whatever the last nested deploy shipped, and
  `gelpack_root()` in prod resolves the LEGACY nested dir with a DeprecationWarning on stderr.
- `check_prod_parity` (automation_health rule 9b) still walks the prod tree, so a stale nested prod
  copy keeps showing up there as drift — that is the intended signal, not something to silence.

## What it checks, precisely

| Kind | Blocking | Meaning |
|---|---|---|
| `MISSING_PROVIDER` | yes (if new) | A first-party module/submodule the consumer imports does not exist in the destination tree after this deploy. Names the consumer, the module, the line, and the file being left behind. |
| `MISSING_SYMBOL` | yes (if new) | The provider file exists in the destination tree but its copy does not define the imported name — the 09-02 shape. Names consumer, symbol, provider, and the destination file inspected. |
| `UNPARSEABLE` | yes | A `.py` in the copy set does not parse; it would die on import in prod. |
| `CHECKER_FAILED` | yes | The child crashed, timed out (300 s) or returned nothing. 🔴 **Fails closed** — the import closure is UNKNOWN, and unknown is not green (HEARTBEAT_RULES rule 1). |
| *pre-existing* | no | Same violation already present in prod before this deploy (N5). Printed. |
| *unchecked* | no | Could not be established without executing code (N3), or an optional `try/except ImportError` dependency. Printed. |

Scope: the **exact copy set** `--apply` would ship (`copy_set()` is shared by the deploy and the
preflight, so the thing graded is the thing that ships). `*_RULES.md` files ride along with the code
and have no import closure — skipped. `if TYPE_CHECKING:` imports never execute and are ignored.
Imports inside function bodies **are** checked: the 09-02 credentials import was one.

## Known limits — read before trusting a PASS

- **One direction only.** The check asks "can prod run the files I am shipping?" It does **not** ask
  "does shipping this provider break a prod consumer I am *not* shipping" (a removed symbol). That
  reverse direction is unguarded; `automation_health.check_prod_parity` (rule 9b) is what surfaces
  the resulting staleness, after the fact.
- **Import-time resolution only.** It cannot see call-signature drift (09-02 instance 3, the
  `GoogleIntegration` `TypeError`), attribute access on an imported module, or the import-ORDER class
  (gotcha D3, where every symbol resolves and the behaviour is still wrong).
- **A PASS is a statement about imports, not about behaviour.** Rule 19's entry-point check and rule
  9b's reachability split remain the checks that grade what prod actually executes.

## Run

```
python scripts/deploy_prod.py                                        # dry-run + preflight report
python scripts/deploy_prod.py --only "scripts/deploy_prod.py" --apply
```

Exit codes: `0` clean/deployed · `1` drift found (dry-run) · `2` prod-newer refusal ·
`3` import-preflight refusal.

## Tests

`tests/test_deploy_prod.py` (copy/refusal guardrails) and
`tests/test_deploy_prod_import_preflight.py` (this doc's rules, one class per property: missing
provider refused, valid control passes, byte-identity cannot save it, isolation and path proof,
no execution of checked code, pre-existing not blocking, optional imports, resolver parity).
