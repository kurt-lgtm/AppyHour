"""AppyHour — shared business logic modules.

Public surface:
  • paths.db_path()                  — canonical shipping.db location
  • paths.invoices_dir()             — carrier-invoice landing dir
  • box_classify.classify_box(...)   — order line-items → box-type bucket
  • internal_classify.is_internal(...) — flag staff/test shipments
  • EXEMPT_INVOICE_ACCOUNTS          — invoice account numbers to skip

  • credentials.get_shopify_credentials() — Shopify auth from env/settings
  • weather.fetch_weather_by_zip(...)     — OWM 5-day forecast
"""
from __future__ import annotations

# Invoice account numbers that are NOT shipping accounts and should be skipped
# at the IMAP/parser layer. H2E101 is the office printer rental UPS account —
# invoices have $0 Amount Due and zero shipment rows. Empirically confirmed
# 2026-05-14 across 13 historical files.
#
# Add new entries here when a non-shipping account starts emitting invoices.
EXEMPT_INVOICE_ACCOUNTS: frozenset[str] = frozenset({
    "H2E101",
})
