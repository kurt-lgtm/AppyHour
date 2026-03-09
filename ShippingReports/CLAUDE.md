# ShippingReports

## Overview
Analytics pipeline for subscription box shipping optimization. Ingests carrier invoices (OnTrac, UPS, FedEx), customer issue data (Gorgias), and tracking events (Parcel Panel) to generate routing recommendations, cost analysis, and performance reports.

## Architecture
- `parsers/` — Standardized invoice/data parsers (OnTrac CSV, UPS CSV, FedEx XLSX, Gorgias, Parcel Panel)
- `reports/` — Analysis modules (cost, transit, misrouting, weather normalization, zip-level)
- `data/` — Raw invoice/issue files (symlinked or copied from GelPackCalculator/Invoices)
- `output/` — Generated reports and routing config recommendations

## Relationship to GelPackCalculator
This project analyzes historical data and outputs routing config recommendations.
GelPackCalculator (`../GelPackCalculator/`) is the real-time execution app that applies those configs to live Shopify orders.

Flow: Invoices → ShippingReports → routing_config.json → GelPackCalculator imports as profile

## Build & Run
```
python -m reports.weekly    # Generate weekly report
python -m reports.recommend # Generate routing config recommendation
python ingest.py            # Parse new invoice files
```

## Dependencies
- Python 3.x (Anaconda: `/c/Users/Work/anaconda3/python.exe`)
- openpyxl (for FedEx XLSX parsing)
- requests (for Gorgias API, future)
- No paid services required (n8n alternative: native Python scheduling)

## Hub Definitions
- Dallas (TX) — Garland, TX 75042. Ships OnTrac, UPS, FedEx. Only hub on Tuesdays.
- Nashville (TN) — Nashville, TN 37210. Ships OnTrac, FedEx. Primary eastern hub.
- Anaheim (CA) — Anaheim, CA. Ships OnTrac, FedEx. West coast hub.
- Indianapolis (IN) — Indianapolis, IN 46204. FedEx overflow for rural eastern zips.
- Woburn (MA) — Company HQ, not a fulfillment hub. Ignore in analysis.

## Carrier Invoice Formats
- OnTrac: CSV, hub in Reference1 field, has First Scan/POD DateTime for transit calc
- UPS: CSV (Invoice_000000C411H40*.csv), hub in Reference No.2, no delivery date
- FedEx: XLSX (AHB_*_FedEx Shipping Breakdown*.XLSX), hub from Shipper City/State, has POD date
