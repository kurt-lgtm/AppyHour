"""Merge multiple shipments JSON files (dedup by carrier+tracking)."""
import json, sys, os
out = sys.argv[1]
seen = set()
merged = []
for path in sys.argv[2:]:
    with open(path) as f:
        for s in json.load(f)['shipments']:
            k = (s['carrier'], s['tracking'])
            if k in seen:
                continue
            seen.add(k)
            merged.append(s)
with open(out, 'w') as f:
    json.dump({'shipments': merged}, f)
print(f"merged {len(merged)} shipments -> {out}")
