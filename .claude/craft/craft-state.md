# Craft State — Enrich Gorgias Rows + Fix Ops Summary Pipeline

## Feature
Enrich Gorgias-synced rows (941+), fix SUMPRODUCT cost formulas, build Shipments tab pipeline

## Mode
FULL

## Current Phase
COMPLETE

## Plan Path
User-provided plan in conversation. Tasks tracked via TaskCreate.

## Steps
1. ✓ Fix SUMPRODUCT cost formula bug — write numeric values in column B (not $-prefixed text)
2. ✓ Build row enrichment function for rows 941+ missing fields
3. ✓ Build Shipments tab pipeline — count Shopify fulfilled orders by FC tag per week
4. ✓ Register new MCP tools and verify end-to-end
5. ✓ Code review — fixed HIGH issues (province mapping, Gorgias search, logging)

## Resume Directive
All tasks complete. Ready for commit.

## Key Decisions
- ops_summary_builder.py line 336: `f"${cost:.1f}"` → plain numeric `cost`
- Enrichment searches Gorgias by order number, Shopify for carrier/FC
- Shipments pipeline queries Shopify fulfilled orders by FC tag prefixes
- Cost rows 70/71 also need numeric values (not $-prefixed text)
