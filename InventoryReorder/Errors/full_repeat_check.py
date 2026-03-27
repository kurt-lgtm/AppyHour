"""Find customers with near-full recipe overlap (5+ food items same as last month)."""
import csv

CSV_PATH = r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\Errors\recharge-errors-2026-03-21.csv"
IGNORE_PREFIXES = ("AHB-", "PK-", "PR-CJAM", "CEX-", "EX-", "BL-")

results = []
with open(CSV_PATH, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        repeat_skus = [s.strip() for s in row["Repeat SKUs"].split(",")]
        food_repeats = [s for s in repeat_skus if not any(s.startswith(p) for p in IGNORE_PREFIXES)]
        results.append({
            "email": row["Email"],
            "order": row["Current Order"],
            "prev": row["Previous Order"],
            "food_repeat_count": len(food_repeats),
            "food_repeats": food_repeats,
        })

results.sort(key=lambda x: -x["food_repeat_count"])

print(f"{'Order':<12} {'Prev':<12} {'#Repeats':>8}  {'Email':<35} Food Repeats")
print("-" * 120)
for r in results:
    repeats_str = ", ".join(r["food_repeats"])
    print(f"{r['order']:<12} {r['prev']:<12} {r['food_repeat_count']:>8}  {r['email']:<35} {repeats_str}")
