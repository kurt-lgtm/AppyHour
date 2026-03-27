"""One-off script to test food safety sync with dedup against existing data."""
import sys
import json
import re

sys.path.insert(0, "AppyHourMCP/tools")
sys.path.insert(0, "../GelPackCalculator")

from gorgias_sheets_sync import sync_food_safety_to_sheet

# --- Step 1: Dry run ---
print("=== Step 1: Syncing (dry run, 60 days) ===")
result = sync_food_safety_to_sheet(days_back=60, dry_run=True)
rows = result["rows"]
print(f"Found {len(rows)} candidate rows")

# --- Step 2: Load existing Gorgias links for ticket-ID-based dedup ---
# These are ALL Gorgias links from the existing UPDATE_Food Safety tab (111 rows)
existing_links_raw = [
    "206575886", "207070902", "207072610", "207028043", "207314259",
    "207295453", "207304927", "207307077", "207410640", "207375231",
    "207481174", "207553254", "207695189", "208368871", "209284140",
    "210274442", "212786986", "212768669", "212948904", "213315452",
    "213595653", "213799272", "215225832", "215480591", "216612382",
    "216887894", "218211100", "217969258", "217592949", "219148991",
    "218793860", "220628932", "220037721", "221362137",
    "220913421", "224093355", "223751019", "225500726",
    "226190925", "226012307", "226205323", "224902528", "225566376",
    "224910456", "227420412", "227375555", "226786723", "226582766",
    "228669140", "227844084", "230083032", "231215858", "231217442",
    "231363725", "231079546", "234025048", "234971774", "235014803",
    "235157255", "235220139", "235827824", "235535150", "235269998",
    "235729844", "236337815", "236488715", "236606352", "236608096",
    "236620509", "236909755", "236883660", "237836421", "238430035",
    "241785979", "247327326", "248323608", "250753526", "252161589",
    "253560445", "254151419", "254673101", "254949593", "256519284",
    "256782262", "257766428", "258828230", "258836408", "259013137",
    "259234500", "259271718", "249957140", "250361096", "250404949",
    "252133447", "252716622", "253961246", "254701141", "255509091",
    "255738532", "256156407", "257374758", "259469868", "261753899",
    "261596203", "261916458", "262388385",
]
existing_ticket_ids = set(existing_links_raw)

# Existing order numbers (without #)
existing_orders_raw = {
    "45389", "52839", "51138", "52982", "52819", "52828", "53138", "53175",
    "52057", "53024", "53357", "52247", "52941", "53377", "53879", "56571",
    "58637", "58503", "57870", "56976", "56344", "57794", "60895", "51533",
    "62831", "62210", "61140", "63465", "62526", "61458", "63750", "62369",
    "66618", "65726", "66458", "68690", "67968", "69781", "69385", "66349",
    "71576", "69968", "69442", "69233", "71946", "72766", "70538", "69405",
    "67862", "71466", "75297", "76637", "70292", "72382", "74618", "78506",
    "79827", "80907", "78715", "78617", "80872", "81102", "80824", "81623",
    "82247", "82358", "81318", "81953", "81340", "81445", "81802", "82076",
    "82103", "86891", "88117", "93825", "109175", "97722", "101464", "103144",
    "100305", "100264", "107977", "108398", "101244", "113177", "110827",
    "108196", "108428", "112395", "96587", "94365", "97050", "88254",
    "101630", "90438", "100662", "106254", "105791", "108689", "110187",
    "104046", "116980", "113266", "111548",
}

print(f"Existing: {len(existing_ticket_ids)} ticket IDs, {len(existing_orders_raw)} orders")

# --- Step 3: Dedup by ticket ID and order number ---
final_rows = []
for row in rows:
    glink = row[9]
    onum = row[1]

    # Extract ticket ID from Gorgias link
    m = re.search(r"/(\d{6,})/?$", glink)
    tid = m.group(1) if m else ""

    if tid and tid in existing_ticket_ids:
        print(f"  DUPE (ticket {tid}): {row[3]}")
        continue

    onum_bare = onum.lstrip("#")
    if onum_bare and onum_bare in existing_orders_raw:
        print(f"  DUPE (order {onum}): {row[3]}")
        continue

    final_rows.append(row)

print(f"\nAfter dedup: {len(final_rows)} new rows")

# --- Step 4: Renumber IDs starting from 112 (existing has 111 rows) ---
next_id = 112
for row in final_rows:
    row[0] = str(next_id)
    next_id += 1

# --- Step 5: Print final rows ---
print("\n=== Final rows for TEST tab ===")
for r in final_rows:
    none_str = "(none)"
    print(
        f"  ID={r[0]} Order={r[1] or none_str} "
        f"Customer={r[3]} Product={r[4] or none_str} "
        f"FC={r[6] or none_str} Tracking={r[7] or none_str} "
        f"Concern={r[8]}"
    )

# Save for writing via MCP
with open("test_food_safety_rows.json", "w") as f:
    json.dump(final_rows, f, indent=2)
print(f"\nSaved {len(final_rows)} rows to test_food_safety_rows.json")
