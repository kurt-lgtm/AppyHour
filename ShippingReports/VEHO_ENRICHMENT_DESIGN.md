# Veho Delivery-Date Enrichment — Design Memo

**Date:** 2026-04-22
**Status:** Design only — not built
**Problem:** Veho invoices have no POD (proof-of-delivery) dates. Tracking prefix `VH*` does not match Shopify `fulfillments.tracking_number`. Result: 0% reship visibility on 2,172 Veho shipments ($6.04 avg, cheapest carrier).

---

## Investigation Findings

### Veho invoice schema (29 cols, sheet "Query result")
Key fields from `AHB_00305_Veho Shipping Breakdown_AHB_4-13-26.xlsx`:

- `Tracking ID` — e.g. `VH67Z3YMG8NK5Y` (Veho-native, NOT Shopify fulfillment tracking)
- `Package ID` — e.g. `PKG_01KNX4DFJSMCTHYGX614HK5HG7` (Veho-internal ULID)
- `External ID` — **EMPTY across 100% of rows** in all 5 readable invoices (3-9, 3-16, 3-23, 3-30, 4-13). Column exists but never populated.
- `Bar Code` — e.g. `5883bd55a155123e4` (hex-ish, unknown if matches any Shopify field)
- `Tendered Timestamp` — ship date (populated)
- `Created Timestamp` — present
- No POD / delivered_at / actual_delivery column

### Fill rate audit (sample)
```
3-9:  104 rows, External ID filled = 0
3-16: 587 rows, External ID filled = 0
3-23: 539 rows, External ID filled = 0
3-30: 369 rows, External ID filled = 0
4-13: 573 rows, External ID filled = 0
```

**Conclusion: Path (b) external-ID → Shopify join is DEAD.** No join key exists in invoice data.

### How UPS/FedEx currently enrich (for reference)
`enrich_ups_delivery.py` pulls Shopify fulfilled orders, builds `tracking_number -> delivery_date` map from `fulfillments[].tracking_number` + `shipment_status == "delivered"`. Works because UPS tracking numbers in Shopify match UPS tracking numbers on invoice.

**Open question for Veho:** Does Shopify `fulfillments.tracking_number` contain the `VH*` Veho ID, or a different carrier handoff ID? Needs a 5-minute check: pull 10 recent Shopify orders with Veho fulfillments and compare `tracking_number` to invoice `Tracking ID`. If match — enrichment is trivial (copy/adapt `enrich_ups_delivery.py`). If not — must go Parcel Panel.

---

## Recommended Path

### Step 1 (cheap validation, 15 min): Verify `VH*` match
Before building anything, confirm whether Shopify `fulfillments.tracking_number` contains the Veho `VH*` ID.

- Pull last 30d of Shopify fulfilled orders tagged with Veho shipments (or filter by tracking prefix `VH`).
- If `VH*` tracking numbers present in Shopify fulfillments with `shipment_status == "delivered"` → **use path (c) Shopify fulfillment match**, clone `enrich_ups_delivery.py` → `enrich_veho_delivery.py`. Done.
- If `VH*` absent from Shopify → proceed to Step 2.

### Step 2 (if Step 1 fails): Parcel Panel integration
Parcel Panel tracks `VH*` natively (confirmed Veho is a supported carrier in most tracking aggregators).

- Add `parsers/parcel_panel.py` OR direct Parcel Panel API client.
- Build `tracking -> delivery_date` map keyed on `VH*`.
- Wire into `ingest.py` as post-parse enrichment step.

### Priority: Step 1 first (path c). Step 2 is fallback.

---

## Next Concrete Action

Run a Shopify query (adapt `enrich_ups_delivery.py` lines 37-66):
```python
# Pull 30d fulfilled orders
# Filter fulfillments where tracking_number startswith "VH"
# Print count + 5 examples
```

If count > 0 → Path (c) viable. Clone enricher. ~1hr work.
If count == 0 → Path (a) Parcel Panel. ~1 day work (API auth, rate limits, schema).

---

## Files Referenced
- `parsers/veho.py` — parser, line 170 sets `delivery_date=None`
- `enrich_ups_delivery.py` — existing pattern to clone
- `GelPackCalculator/Invoices/AHB_00305_Veho Shipping Breakdown_AHB_4-13-26.xlsx` — reference invoice

## Out of Scope
- Retrofitting Veho shipments older than Shopify fulfillment retention window
- Manual POD entry (not scalable)
