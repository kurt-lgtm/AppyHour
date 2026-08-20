"""Load FedEx invoice detail CSVs (FedEx Billing Online extract) into canonical shipments.db.

🔴 GOTCHA THIS FILE EXISTS TO NOT REPEAT (2026-07-30):
This script used to map columns POSITIONALLY off a hand-written index list
(`r[37]` = "Recipient State", `r[63]` = "Zone Code", ...). The real extract is 210
columns wide and carries a `Service Packaging` column at index 27, so every index at
or past 27 in that list was one short. Result: `state` got the CITY, `zip_code` got the
STATE, `zone` got the COUNTRY, and the real ZIP was thrown away — 1,368 FedEx rows
across the four May invoices below, plus a sibling bug in
`load_ups_invoices_with_weight.py`. sqlite is typeless so it accepted all of it silently;
it surfaced only in July when MySQL's `varchar(8)` on `shipments.state` rejected the
oversized values during the managed-DB migration.

  - NEVER index invoice columns by position. FedEx reorders/inserts columns between
    extract versions without renaming anything. Map by header name (`csv.DictReader`).
  - Every row goes through `appyhour_lib.shipment_validate` before the INSERT; a row whose
    `state` is not 2 letters or whose `zip_code` is not 5 digits is REJECTED, not written,
    and the reject count is printed. MySQL no longer catches this (column widened to
    varchar(32) so the ETL can mirror verbatim) — this gate is the only net left.

Filter: only rows with a tracking ID (skips pickup/fuel-only billing events).

🔴 GOTCHA #2 — CONTROL TOTAL (added 2026-08-09):
A short load is silent. If FedEx hands us 3,048 lines and we ingest 2,900, nothing in the old
code noticed: no exception, no count mismatch, just a quietly understated cost history feeding
the engine's lane economics. The "Auto FedExInv" extract carries the invoice's own stated total
IN BAND (`Original Amount Due`), so the file can check itself:

    sum(Net Charge Amount) over an invoice's rows == Original Amount Due  (to the cent)

  - Every invoice carrying the column is asserted. A mismatch is a LOUD FAILURE naming the
    invoice and the delta — the load ABORTS, it is never partially written.
  - The manual Billing Center export ("FedEx_invoice_<date>.CSV") LACKS the column. Those
    invoices are reported UNVERIFIABLE by name, never silently waved through. An unverifiable
    load must be visibly unverifiable.
  - CROSS-FILE TRANSFER: when a richer Auto file in the same batch states a total for an invoice
    that also appears in a manual export, that total is applied to the manual copy too. This is
    how the 11-invoice manual export gets 4 of its invoices verified for free.
  - Do NOT "fix" a mismatch by loosening the tolerance. A mismatch means the extract is short,
    duplicated, or column-shifted — all three have happened here.

🔴 GOTCHA #3 — DEDUPE KEY (added 2026-08-09):
FedEx REUSES tracking numbers over time ([[tracking-reuse-and-woburn-ice-releg]]), so `tracking`
alone is NOT a stable identity. Dedupe is on **(invoice_id, tracking)** — in-batch and against
the DB — so re-loading the same invoice is idempotent rather than duplicative.
  ⚠️ SCHEMA CAVEAT: `shipments` still carries `idx_shipments_tracking UNIQUE(tracking)`, so the
  DB physically cannot hold the same tracking under two invoices even though that is legitimate
  under reuse. The loader therefore also detects "tracking present under a DIFFERENT invoice"
  and reports those rows as a BLOCKED conflict instead of letting sqlite raise an opaque
  IntegrityError mid-executemany. Relaxing that index to UNIQUE(invoice_id, tracking) is a
  schema migration and a Kurt decision — not something this loader does on its own.

Dry-run is the DEFAULT. Writing requires an explicit --commit.
"""
import argparse
import csv
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, r"C:\Users\Work\Claude Projects\AppyHour")
from appyhour_lib.shipment_validate import partition, report_rejects  # noqa: E402

CSV_FILES = [
    r"C:\Users\Work\Downloads\2026-05-18 10-32 Auto FedExInv 203738113.CSV",
    r"C:\Users\Work\Downloads\2026-05-19 10-59 Auto FedExInv 203738113.CSV",
    r"C:\Users\Work\Downloads\2026-05-25 10-55 Auto FedExInv 203738113.CSV",
    r"C:\Users\Work\Downloads\2026-05-25 11-58 Auto FedExInv 203738113.CSV",
]

# In-band invoice control total. Present in the "Auto FedExInv" extract, absent from the
# manual Billing Center export. Matched BY NAME — never by position (see gotcha #1).
CONTROL_TOTAL_HEADER = "Original Amount Due"
CENT_TOLERANCE = 1  # cents


class ControlTotalMismatch(RuntimeError):
    """An invoice's line sum did not equal the total FedEx stated for it. Never swallow this."""

DB = (os.environ.get("APPYHOUR_DB_PATH") or (r"C:\AppyHourData\shipping.db" if os.path.exists(r"C:\AppyHourData\shipping.db") else os.path.expandvars(r"%APPDATA%\AppyHour\shipping.db")))

# Header names, not indices. Every one of these is verified present before a file is parsed.
REQUIRED_HEADERS = (
    "Invoice Number",
    "Express or Ground Tracking ID",
    "Net Charge Amount",
    "Service Type",
    "Shipment Date",
    "Actual Weight Amount",
    "Rated Weight Amount",
    "Recipient City",
    "Recipient State",
    "Recipient Zip Code",
    "Zone Code",
)


def _f(x):
    try:
        return float(x) if x and x.strip() else None
    except (ValueError, AttributeError):
        return None


def _date(s):
    s = (s or "").strip()
    if len(s) != 8:
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _s(row, key):
    return (row.get(key) or "").strip()


def _cents(x):
    """Money -> integer cents. Float sums drift; the control-total assert must not."""
    s = (x or "").strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return round(float(s) * 100)
    except ValueError:
        return None


def parse_invoice(filepath):
    """Header-driven parse. Raises KeyError if the extract lacks an expected column,
    rather than silently reading whatever happens to sit at some index.

    Returns (rows, stated_totals, parsed_totals, line_counts).
      stated_totals  invoice_number -> cents FedEx itself states (in-band); {} when the
                     extract lacks the column (manual Billing Center export).
      parsed_totals  invoice_number -> summed cents over EVERY line we read, including the
                     no-tracking pickup/fuel lines this loader does not ingest. The control
                     total certifies the FILE was read whole; it is not a check on what we
                     chose to load. Summing only loadable rows would make a dropped fuel line
                     look like a short read.
      line_counts    invoice_number -> total lines seen (loadable or not)."""
    rows = []
    stated = {}
    parsed = defaultdict(int)
    line_counts = defaultdict(int)
    with open(filepath, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        missing = [h for h in REQUIRED_HEADERS if h not in fields]
        if missing:
            raise KeyError(f"{Path(filepath).name}: missing expected column(s): {missing}")
        has_control = CONTROL_TOTAL_HEADER in fields
        for r in reader:
            inv_no = _s(r, "Invoice Number")
            line_counts[inv_no] += 1
            parsed[inv_no] += _cents(r.get("Net Charge Amount")) or 0
            if has_control:
                stated_c = _cents(r.get(CONTROL_TOTAL_HEADER))
                if inv_no and stated_c is not None:
                    prev = stated.get(inv_no)
                    if prev is not None and prev != stated_c:
                        raise ControlTotalMismatch(
                            f"{Path(filepath).name}: invoice {inv_no} states TWO different "
                            f"control totals in the same file (${prev / 100:,.2f} vs "
                            f"${stated_c / 100:,.2f}). Extract is inconsistent — refusing to load."
                        )
                    stated[inv_no] = stated_c
            tracking = _s(r, "Express or Ground Tracking ID")
            if not tracking:
                continue
            actual_wt = _f(r.get("Actual Weight Amount"))
            rated_wt = _f(r.get("Rated Weight Amount"))
            # Zip arrives as 85614-1432-65 (zip5-plus4-suffix) — keep zip5 only.
            zip5 = _s(r, "Recipient Zip Code").split("-")[0][:5]
            rows.append({
                "invoice_id": _s(r, "Invoice Number"),
                "tracking": tracking,
                "carrier": "FedEx",
                "service": _s(r, "Service Type") or _s(r, "Ground Service"),
                "hub": None,
                "state": _s(r, "Recipient State").upper() or None,
                "zip_code": zip5 or None,
                "city": _s(r, "Recipient City") or None,
                "zone": _s(r, "Zone Code") or None,
                "cost": _f(r.get("Net Charge Amount")) or 0.0,
                # 🔴 `weight` is a COALESCE (rated else actual) kept for back-compat — two facts in
                # one column. `billed_weight` is FedEx's RATED (chargeable) weight, verbatim.
                # See INVOICE_INGEST_RULES §5.1.
                "weight": rated_wt or actual_wt,
                "billed_weight": rated_wt,
                # The thin Billing Online export states POD per tracking and it was being dropped.
                # HISTORY ONLY — invoices lag a billing cycle, so this never feeds a live/tracking
                # path (§9, Kurt 2026-08-20).
                "delivery_date": _date(r.get("POD Delivery Date")),
                "actual_weight": actual_wt,
                "rated_weight": rated_wt,
                "dim_l": _f(r.get("Dim Length")),
                "dim_w": _f(r.get("Dim Width")),
                "dim_h": _f(r.get("Dim Height")),
                "dim_div": _f(r.get("Dim Divisor")),
                "ship_date": _date(r.get("Shipment Date")),
                "source_file": Path(filepath).name,
                # 🔴 NEVER default the account. This line used to read
                #     _s(r, "Bill to Account Number") or "203738113"
                # which silently stamped OUR account onto any row whose column was blank —
                # a fabricated attribution that is indistinguishable from a real one afterwards,
                # and part of why shipments.acct could not be trusted (INVOICE_INGEST_RULES §1,
                # ~/.claude/rules/never-fabricate.md). Blank stays blank; acct_canon decides.
                "acct": _s(r, "Bill to Account Number") or None,
                # Provenance for the dedupe tie-break: a row from a control-total-carrying
                # extract is the verified copy and wins over the same row from a manual export.
                "_verified_source": has_control,
            })
    return rows, stated, dict(parsed), dict(line_counts)


def check_control_totals(stated, parsed, line_counts, sources):
    """Assert sum(Net Charge Amount) == Original Amount Due per invoice, to the cent.

    Returns (results, verified, unverifiable, failed). Callers MUST abort on `failed` —
    a mismatch means the extract is short, duplicated, or column-shifted.
    An invoice with no stated total is reported UNVERIFIABLE by name; it is never counted
    as a pass and never silently omitted from the report."""
    results = []
    for inv_no in sorted(parsed):
        got = parsed[inv_no]
        want = stated.get(inv_no)
        src = sources.get(inv_no, "?")
        if want is None:
            status, delta = "UNVERIFIABLE", None
        else:
            delta = got - want
            status = "PASS" if abs(delta) <= CENT_TOLERANCE else "FAIL"
        results.append({
            "invoice": inv_no, "status": status, "parsed_cents": got, "stated_cents": want,
            "delta_cents": delta, "lines": line_counts.get(inv_no, 0), "control_source": src,
        })
    verified = [r for r in results if r["status"] == "PASS"]
    unverifiable = [r for r in results if r["status"] == "UNVERIFIABLE"]
    failed = [r for r in results if r["status"] == "FAIL"]
    return results, verified, unverifiable, failed


def print_control_report(results, failed, unverifiable):
    print("\n=== CONTROL TOTALS (sum of Net Charge Amount vs FedEx's stated Original Amount Due) ===")
    for r in results:
        want = f"${r['stated_cents'] / 100:,.2f}" if r["stated_cents"] is not None else "NONE"
        line = (f"  {r['status']:<13} invoice {r['invoice']}  lines={r['lines']:<5} "
                f"sum=${r['parsed_cents'] / 100:>10,.2f}  stated={want:>12}  [{r['control_source']}]")
        if r["status"] == "FAIL":
            line += f"  DELTA=${r['delta_cents'] / 100:+,.2f}"
        print(line)
    if unverifiable:
        print(f"\n  ⚠️  {len(unverifiable)} invoice(s) carry NO control total "
              f"({', '.join(r['invoice'] for r in unverifiable)}).")
        print("      This extract cannot prove it was read whole. The load is UNVERIFIED for "
              "those invoices — re-pull them as an 'Auto FedExInv' extract to verify.")
    if failed:
        print(f"\n  🔴 {len(failed)} CONTROL-TOTAL FAILURE(S):")
        for r in failed:
            print(f"      invoice {r['invoice']}: parsed ${r['parsed_cents'] / 100:,.2f} vs stated "
                  f"${r['stated_cents'] / 100:,.2f} (delta ${r['delta_cents'] / 100:+,.2f}, "
                  f"{r['lines']} lines read)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*", default=None,
                    help="FedEx invoice CSVs (default: the CSV_FILES list in this module)")
    ap.add_argument("--commit", action="store_true",
                    help="Actually write to the DB. WITHOUT THIS THE RUN IS A DRY RUN.")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="Permit --commit of invoices that carry no control total (manual export). "
                         "The unverifiable invoices are still named in the report.")
    args = ap.parse_args(argv)
    files = args.files or CSV_FILES
    dry = not args.commit
    print(f"MODE: {'DRY RUN (no writes)' if dry else '🔴 COMMIT (writes to DB)'}   DB={DB}")

    all_rows = []
    stated, parsed, line_counts, sources = {}, defaultdict(int), defaultdict(int), {}
    for f in files:
        rows, f_stated, f_parsed, f_lines = parse_invoice(f)
        name = Path(f).name
        print(f"{name}: {len(rows)} shipment rows, {sum(f_lines.values())} lines, "
              f"control totals: {'IN-BAND' if f_stated else 'ABSENT'}")
        all_rows.extend(rows)
        for inv_no, c in f_stated.items():
            prev = stated.get(inv_no)
            if prev is not None and prev != c:
                raise ControlTotalMismatch(
                    f"invoice {inv_no}: two files state different control totals "
                    f"(${prev / 100:,.2f} vs ${c / 100:,.2f}) — refusing to load.")
            if prev is None:
                stated[inv_no] = c
                # CROSS-FILE TRANSFER: note which file vouched for this invoice's total.
                sources[inv_no] = f"in-band:{name}"
        # parsed sums are per (file, invoice); an invoice appearing in two files is summed
        # per file and reconciled below so a duplicated file cannot inflate the check.
        for inv_no, c in f_parsed.items():
            if inv_no in parsed and parsed[inv_no] != c:
                print(f"  ⚠️  invoice {inv_no} appears in more than one file with DIFFERENT line "
                      f"sums (${parsed[inv_no] / 100:,.2f} vs ${c / 100:,.2f}) — using the larger; "
                      f"the control-total check will catch a short copy.")
            parsed[inv_no] = max(parsed.get(inv_no, 0), c)
            line_counts[inv_no] = max(line_counts.get(inv_no, 0), f_lines[inv_no])
    for inv_no in parsed:
        sources.setdefault(inv_no, "NONE (manual export — no control total)")
    print(f"\nTotal: {len(all_rows)} shipment rows across {len(files)} file(s)")

    # 🔴 CONTROL-TOTAL GATE — see gotcha #2. Runs BEFORE any dedupe or write.
    results, verified, unverifiable, failed = check_control_totals(
        stated, dict(parsed), dict(line_counts), sources)
    print_control_report(results, failed, unverifiable)
    print(f"\n  verified={len(verified)}  unverifiable={len(unverifiable)}  failed={len(failed)}")
    if failed:
        raise ControlTotalMismatch(
            f"{len(failed)} invoice(s) failed the control-total assert "
            f"({', '.join(r['invoice'] for r in failed)}). NOTHING was written. "
            f"Re-pull the extract; do not loosen the tolerance.")
    if unverifiable and not dry and not args.allow_unverified:
        raise SystemExit(
            f"\n🔴 REFUSING TO COMMIT: {len(unverifiable)} invoice(s) carry no control total "
            f"({', '.join(r['invoice'] for r in unverifiable)}). Re-pull them as an 'Auto "
            f"FedExInv' extract, or pass --allow-unverified to load them knowingly unverified.")

    # 🔴 Dedup within batch on (invoice_id, tracking) — NOT tracking alone (gotcha #3).
    # Tie-break, in order: the control-total-verified copy wins (a manual export and an Auto
    # extract of the SAME invoice are the same row, but only one of them was proven whole),
    # then higher cost, then the one carrying a weight.
    def _rank(r):
        return (bool(r["_verified_source"]), r["cost"] or 0.0, bool(r["weight"]))

    seen = {}
    for r in all_rows:
        k = (r["invoice_id"], r["tracking"])
        if k not in seen or _rank(r) > _rank(seen[k]):
            seen[k] = r
    batch = list(seen.values())
    kept_verified = sum(1 for r in batch if r["_verified_source"])
    print(f"\nAfter in-batch dedup on (invoice_id, tracking): {len(batch)} unique rows "
          f"({len(all_rows) - len(batch)} duplicate line(s) collapsed; "
          f"{kept_verified} kept from a control-total-verified extract)")

    # 🔴 Validation gate — reject malformed rows instead of writing them.
    batch, rejected = partition(batch)
    print(report_rejects(rejected, label="FedEx invoice "))

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True) if dry else sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(invoice_id,''), tracking FROM shipments WHERE carrier='FedEx'")
    db_pairs = set(cur.fetchall())
    db_tracking_owner = {}
    for inv_no, trk in db_pairs:
        db_tracking_owner.setdefault(trk, inv_no)

    new_rows, dupes, conflicts = [], [], []
    for r in batch:
        if (r["invoice_id"], r["tracking"]) in db_pairs:
            dupes.append(r)
        elif r["tracking"] in db_tracking_owner:
            # Same tracking, DIFFERENT invoice. Legitimate under FedEx tracking reuse, but
            # UNIQUE(tracking) makes it unwritable. Surface it; never let sqlite fail opaquely.
            conflicts.append((r, db_tracking_owner[r["tracking"]]))
        else:
            new_rows.append(r)
    print(f"\nWOULD INSERT: {len(new_rows)}   skipped as duplicate (invoice_id,tracking): "
          f"{len(dupes)}   blocked by UNIQUE(tracking) conflict: {len(conflicts)}")
    if conflicts:
        print("  🔴 UNIQUE(tracking) CONFLICTS — tracking already in DB under a different invoice.")
        print("     This is FedEx tracking reuse meeting idx_shipments_tracking. Relaxing that")
        print("     index to UNIQUE(invoice_id, tracking) is a schema migration + Kurt decision.")
        for r, owner in conflicts[:10]:
            print(f"       {r['tracking']}: file invoice {r['invoice_id']} vs DB invoice {owner}")
        if len(conflicts) > 10:
            print(f"       ... and {len(conflicts) - 10} more")

    # Spot check before insert
    if new_rows:
        sample = new_rows[0]
        print(f"\nSample new row: invoice={sample['invoice_id']}, tracking={sample['tracking']}, "
              f"city={sample['city']}, state={sample['state']}, zip={sample['zip_code']}, "
              f"zone={sample['zone']}, cost=${sample['cost']}, actual_wt={sample['actual_weight']}, "
              f"rated_wt={sample['rated_weight']}, dim={sample['dim_l']}x{sample['dim_w']}x{sample['dim_h']}/{sample['dim_div']}")

    # Coverage by ship-week that this load WOULD produce.
    wk = defaultdict(lambda: [0, 0])
    for r in new_rows:
        if r["ship_date"]:
            d = date.fromisoformat(r["ship_date"])
            monday = (d - timedelta(days=d.weekday())).isoformat()
            wk[monday][0] += 1
            wk[monday][1] += r["cost"]
    if wk:
        print("\n=== Coverage by ship-week (rows that would be inserted) ===")
        for monday in sorted(wk):
            n, cost = wk[monday]
            print(f"  week of {monday}: {n:>5} rows  ${cost:>10,.2f}")

    if dry:
        print("\n=== DRY RUN — nothing written. Re-run with --commit to load. ===")
    elif new_rows:
        insert_sql = """
        INSERT INTO shipments
        (invoice_id, tracking, carrier, service, hub, state, zip_code, city, zone,
         cost, weight, ship_date, source_file, acct, is_internal)
        VALUES (:invoice_id, :tracking, :carrier, :service, :hub, :state, :zip_code,
                :city, :zone, :cost, :weight, :ship_date, :source_file, :acct, 0)
        """
        cur.executemany(insert_sql, new_rows)
        conn.commit()
        print(f"\nInserted {cur.rowcount} rows.")

    # Dim-billing audit: how many rated_weight > actual_weight?
    dim_billed = sum(1 for r in batch if r["rated_weight"] and r["actual_weight"] and r["rated_weight"] > r["actual_weight"])
    same_wt = sum(1 for r in batch if r["rated_weight"] and r["actual_weight"] and r["rated_weight"] == r["actual_weight"])
    rated_less = sum(1 for r in batch if r["rated_weight"] and r["actual_weight"] and r["rated_weight"] < r["actual_weight"])
    has_dim = sum(1 for r in batch if r["dim_l"] and r["dim_w"] and r["dim_h"])
    print("\n=== Dim-billing audit (batch) ===")
    print(f"Rows with rated > actual (dim-billed): {dim_billed}")
    print(f"Rows with rated == actual:             {same_wt}")
    print(f"Rows with rated < actual (rare):       {rated_less}")
    print(f"Rows with dim data populated:          {has_dim}")

    conn.close()


if __name__ == "__main__":
    main()
