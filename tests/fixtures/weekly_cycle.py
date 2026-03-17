"""Fixture data for weekly fulfillment cycle E2E tests.

Simulates a realistic Friday→Wednesday cycle with inventory, demand,
curation recipes, and order data.
"""

CURATION_RECIPES = {
    "MONG": [("CH-BLR", 1), ("CH-WWDI", 1), ("MT-LONZ", 1), ("AC-DTCH", 1), ("CH-MCPC", 1)],
    "MDT": [("CH-MCPC", 1), ("CH-MSMG", 1), ("MT-TUSC", 1), ("AC-PRPE", 1), ("CH-TTBRIE", 1)],
    "OWC": [("CH-WMANG", 1), ("CH-UCONE", 1), ("MT-LONZ", 1), ("AC-TCRISP", 1), ("CH-MCPC", 1)],
}

PR_CJAM_OVERRIDES = {
    "MONG": "CH-BLR",
    "MDT": "CH-TTBRIE",
    "OWC": "CH-MCPC",
}

CEX_EC_OVERRIDES = {
    "MONG": "CH-WWDI",
    "MDT": "CH-MCPC",
    "OWC": "CH-WMANG",
}

# Starting inventory (Friday morning)
FRIDAY_INVENTORY = {
    "CH-MCPC": 500, "CH-BLR": 300, "CH-WWDI": 200,
    "CH-MSMG": 150, "CH-TTBRIE": 180, "CH-WMANG": 120,
    "CH-UCONE": 90, "MT-LONZ": 250, "MT-TUSC": 180,
    "AC-DTCH": 200, "AC-PRPE": 150, "AC-TCRISP": 100,
}

# Simulated subscription demand (boxes per curation for the week)
WEEKLY_DEMAND = {
    "MONG": 80,
    "MDT": 60,
    "OWC": 40,
}

# Sample unfulfilled orders (Shopify-like structure)
SAMPLE_ORDERS = [
    {
        "id": 1001, "name": "#EF-1001", "tags": "", "email": "alice@example.com",
        "line_items": [
            {"sku": "AHB-MCUST-MONG", "quantity": 1, "title": "Custom Med MONG for Life", "price": "79.00"},
            {"sku": "CH-BLR", "quantity": 1, "title": "Blue", "price": "0"},
            {"sku": "CH-WWDI", "quantity": 1, "title": "Wensleydale", "price": "0"},
            {"sku": "MT-LONZ", "quantity": 1, "title": "Lonza", "price": "0"},
            {"sku": "AC-DTCH", "quantity": 1, "title": "Dutch Crackers", "price": "0"},
            {"sku": "CH-MCPC", "quantity": 1, "title": "Manchego", "price": "0"},
            {"sku": "PR-CJAM-MONG", "quantity": 1, "title": "Bonus Pairing", "price": "0"},
            {"sku": "CEX-EC-MONG", "quantity": 1, "title": "Extra Cheese", "price": "0"},
            {"sku": "PK-TGUIDE", "quantity": 1, "title": "Tasting Guide", "price": "0"},
        ],
    },
    {
        "id": 1002, "name": "#EF-1002", "tags": "", "email": "bob@example.com",
        "line_items": [
            {"sku": "AHB-MCUST-MDT", "quantity": 1, "title": "Custom Med MDT for Life", "price": "79.00"},
            {"sku": "CH-MCPC", "quantity": 1, "title": "Manchego", "price": "0"},
            {"sku": "CH-MSMG", "quantity": 1, "title": "Smoked Gouda", "price": "0"},
            {"sku": "MT-TUSC", "quantity": 1, "title": "Tuscan Salami", "price": "0"},
            {"sku": "AC-PRPE", "quantity": 1, "title": "Peppers", "price": "0"},
            {"sku": "CH-TTBRIE", "quantity": 1, "title": "Truffle Brie", "price": "0"},
            {"sku": "PR-CJAM-MDT", "quantity": 1, "title": "Bonus Pairing", "price": "0"},
            {"sku": "PK-TGUIDE", "quantity": 1, "title": "Tasting Guide", "price": "0"},
        ],
    },
    {
        "id": 1003, "name": "#EF-1003", "tags": "reship", "email": "carol@example.com",
        "line_items": [
            {"sku": "AHB-MCUST-OWC", "quantity": 1, "title": "Custom Med OWC", "price": "79.00"},
            {"sku": "CH-WMANG", "quantity": 1, "title": "Mango Stilton", "price": "0"},
        ],
    },
    {
        "id": 1004, "name": "#EF-1004", "tags": "", "email": "dave@example.com",
        "line_items": [
            {"sku": "AHB-MCUST-MONG", "quantity": 1, "title": "Custom Med MONG for Life", "price": "79.00"},
            {"sku": "CH-BLR", "quantity": 1, "title": "Blue", "price": "0"},
            {"sku": "CH-WWDI", "quantity": 1, "title": "Wensleydale", "price": "0"},
            {"sku": "MT-LONZ", "quantity": 1, "title": "Lonza", "price": "0"},
            {"sku": "AC-DTCH", "quantity": 1, "title": "Dutch Crackers", "price": "0"},
            {"sku": "CH-MCPC", "quantity": 3, "title": "Manchego", "price": "0"},
            {"sku": "PR-CJAM-MONG", "quantity": 1, "title": "Bonus Pairing", "price": "0"},
            {"sku": "PK-TGUIDE", "quantity": 1, "title": "Tasting Guide", "price": "0"},
        ],
    },
]
