# KURT-TODO — things only YOU can unblock

*Maintained by Claude — updated every session as items land or appear. Strike-through = done.
Companion: `HANDOFF.md` (session state) · `.claude/plans/2026-06-12-GAP-FIXES-plan.md` (specs).*

## 🔴 Blocking active work
| # | Action | Unblocks | Effort |
|---|---|---|---|
| 1 | **Shopify: add `write_customers` scope** to the API app (Admin → app → API scopes) | G12 address propagation — **11 customers' renewals re-break monthly until done** | ~3 min |
| 2 | **Recharge: grant address WRITE** on the API token (or issue a new token with it) | same G12 propagation (Recharge side) | ~3 min |
| 3 | **Fill `%APPDATA%/AppyHour/portal_creds.json`** — FedEx Billing Online + UPS Billing Center logins (suffix-113 acct; file is ACL'd, never committed) | M2 `portal_pull.py` — automated invoice fetch, kills the ~3-wk FedEx lag | ~5 min |
| 4 | **Create a Slack incoming webhook** (#core-team or an alerts channel) and tell me the URL → I set `AH_SLACK_WEBHOOK` | G3 alerting — escalations reach your phone instead of log files | ~5 min |

## 🔴 Slack answers owed (6/12 triage — suggested replies ready)
| # | Where / who | Ask | Suggested reply |
|---|---|---|---|
| S1 | #reship / Mark | ⏰ TODAY add #150982 to today's RMFG | "Added to today's RMFG" (after adding) |
| S2 | #peak-elevate-foods / Tommy | ⏰ heat-affected box — BLR concerning | "Partial reship BLR only, discard it" |
| S3 | #reship / Mark | one-time reship #148887? | "Yes, one-time reship" |
| S4 | DM Jessa | full refund on triggered-sub customer? | "No — prior box delivered, new ships this week" |
| S5 | #reship / Jessa | Gourmet Bites safe-to-eat macro wording | "Sealed trays + cool ice pack = safe; macro coming" |
| S6 | DM Jessa | tray order: ship this week or change address? | confirm if stale first |

## 📧 Gmail drafts ready to send (6/12)
- Sigma (corrected invoices @ 25.00/m + check new 6/11 invoice) · Q-Sales credit (apply to next invoice) · QProducts (13x9x9/13x8x8 quote nudge)

## 🟡 Decisions / approvals coming up
| # | Action | Context | When |
|---|---|---|---|
| 6b | **Post-ship B1 check (after 6/15 ships):** Wednesday postmortem + D5 fence detector report whether RMFG honored every `!NO` fence in the cheapest-rate era — read that result | first live fences + probation cohort | Wed 6/17 |
| 7 | **Approve scheduled-task repoint to the pinned prod checkout** (`C:\AppyHourProd\` — `deploy_prod.py` BUILT + ran 6/12, checkouts live) — stops the working-tree-is-prod hazard (it ran mid-build code on live 6/11). Just needs your OK to repoint the logon/Wed/weather tasks at it | G4 | ready |
| 8 | **Ask your Veho rep to EMAIL the weekly GroundPlusSuite file** (vs portal download) | zero-auth Veho watcher — simplest version of G6 | whenever |
| 9 | **RMFG conversation: `!ExtraGel48oz_x2!` recipe** — Shopify rejects duplicate tags, so a 2×48oz gel rec needs a new tag RMFG honors | M4 ice enforcement (multi-gel reships) | no deadline |

## ⚪ Standing / occasional
- Click **Run now** once on scheduled task `gorgias-field-gate-daily` — pre-approves its Bash+Slack perms (GAP-01 gate, shipped 6/12).
- Codex session limits: if a Codex batch dies with "session limit", it resets ~2:30 AM ET — relaunch is one ask.

## ✅ Done (recent)
- ~~Approve build go for the coldchain refactor~~ (6/11)
- ~~DIM fields decision~~ → pointer table (6/11)
- ~~Veho new-hub rule~~ → two-gate lane rule set (6/11)
- ~~Express probation decision~~ → live, shadow-validated (6/11)
- ~~Bare-numbers-in-address2 decision~~ → kept bare (6/12)
- ~~GO on invalid-address fixes~~ → 37/37 applied (6/12)
- ~~Approve 6/15 cohort apply~~ → routing + ice applied, 0 fail (6/12)
- ~~Bree/Pam Fixed_Route decision~~ → moot, neither in the 6/15 cohort (6/12)
- ~~Trial-lane concession~~ → tried + insured + tracked (6/12)
- ~~Folds G13 (eff-TNT) + G14 (engine=config)~~ → executed (6/12)
- ~~6/15 trial-ice retrofit~~ → leave as-is, insurance starts clean 6/22 (6/12)
- ~~Offsite backup~~ → automated weekly Sun 02:00 task (6/12)
