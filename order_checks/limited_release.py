"""Limited-release boxes: did the customer get what the description PROMISED?

  python -m order_checks.limited_release --since 2026-08-20 [--out DIR]

A limited-release box (`AHB-X*`) publishes its exact contents on the product page, so the
customer knows precisely what they bought. When we run out and substitute, they SEE it.
Kurt 2026-09-03: "we are explicit about what we're selling on the description BUT sometimes
we do run out ... its like a chosen swap class that we have to notify."

So a missing promised item is either:
  * NEAR-IDENTICAL / same theme -> we did the right thing, no contact
  * anything else               -> OUTREACH, per `swap-notification-classes`

🔴 GOTCHAS:

  * The promised list is parsed from the LIVE product description, never hardcoded. A
    hardcoded list covered 3 of 15 parent SKUs and silently skipped 64 of 76 orders while
    printing a confident "5 missing".
  * Description text and SKU titles disagree on punctuation and spelling: the description
    says "Smokin' Goat", the SKU title is "Smoking Goat". Raw comparison reported 5 false
    substitutions. `_key()` strips apostrophes, punctuation and trailing `*`, and matches
    on a normalised prefix.
  * "(large only)" / "(medium only)" markers scope an item to one variant. Ignoring them
    flags every medium box as missing the large-only cheese.
  * A box may legitimately carry EXTRA items beyond the description -- the customer's own
    portal picks ride along (#180184 shipped the full promised list plus their own Honey &
    Herb Prosciutto). Extras are never a defect; only MISSING promised items are.
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AppyHourMCP"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "AppyHourMCP", "tools"))

from order_edit import shopify_graphql  # noqa: E402

from appyhour_lib.credentials import get_shopify_auth  # noqa: E402

# 🔴 Substitutions the customer is NOT contacted about -- product-identical or so close the
# swap is invisible. SSOT is `~/.knowledge/domain/Swap Rules.md` rule 1b + the
# `swap-notification-classes` memory. Each pair is Kurt's, never inferred:
#   CH-FONT / CH-FONTAL   Fontina / Fontal        (Kurt 2026-09-03)
#   CH-EBRIE / CH-BRIE    E-Brie / Brie           (Kurt 2026-09-03, and 2026-07-31)
#   MT-SBRES / MT-IBRES, MT-BRAS / MT-IBRES, AC-RBOL / AC-FCEVOO   (2026-07-31)
# 🔴 Near-identical is NOT the same as interchangeable elsewhere: AppyHour/CLAUDE.md still
# requires confirming CH-FONT vs CH-FONTAL identity before any SKU operation. This list
# governs OUTREACH duty only.
NEAR_IDENTICAL = [
    {"fontina", "fontal"},
    {"brie", "e-brie", "ebrie"},
    {"beef bresaola", "italian bresaola", "bresaola"},
    {"rustic bakery olive oil", "extra virgin olive oil & sea salt crackers"},
]

VARIANT_ONLY = re.compile(r"\((large|medium|small)\s+only\)", re.I)
CHILD = ("AC-", "CH-", "MT-")

ORDERS = """query($q:String!,$after:String){orders(first:60,query:$q,after:$after){
  pageInfo{hasNextPage endCursor}
  nodes{name createdAt displayFulfillmentStatus tags
    customer{email}
    lineItems(first:80){nodes{sku title currentQuantity}}}}}"""

PRODUCTS = """query($q:String!){products(first:50,query:$q){nodes{
  title descriptionHtml variants(first:20){nodes{sku title}}}}}"""


def _key(s):
    """Normalise a title for comparison across description text and SKU titles."""
    s = unicodedata.normalize("NFKD", (s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))   # jamon != jam n
    s = s.lower().strip().rstrip("*").strip()
    s = VARIANT_ONLY.sub("", s)
    s = re.sub(r"[’'`]", "", s)                            # smokin' -> smokin
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


STOP = {"the", "and", "with", "a", "of", "de", "al", "style"}
# Description wording vs SKU-title wording for the SAME product. Expansions and the one
# synonym Kurt confirmed (2026-09-03: jam/preserves -- "its the same thing").
SYNONYM = {"evoo": "extra virgin olive oil", "preserves": "jam", "marmalade": "jam"}


def _expand(k):
    for a, b in SYNONYM.items():
        k = re.sub(chr(92)+chr(98) + a + chr(92)+chr(98), b, k)
    return k


def _words(s):
    return {w for w in _expand(_key(s)).split() if w not in STOP}


def _same(a, b):
    """Is `b` the same item as `a`? Compares word SETS, not strings.

    🔴 Four spelling/format gaps between description text and SKU titles, each of which
    produced false substitutions before this:
      order   "Truffle Salami"  vs "Salami Truffle"          -- 28 false hits on X12SUMS
      accent  "Jamon Serrano"   vs "Jamón Serrano"      -- 4
      prefix  "Fenugreek Gouda" vs "Farmstead Fenugreek Gouda" -- 4
      apostr. "Smokin' Goat"    vs "Smoking Goat"            -- 5
    Subset either direction counts: the SKU title is often the description name plus a
    producer prefix, and vice versa.
    """
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    if wa <= wb or wb <= wa:
        return True
    # stem-match the remainder, for smokin/smoking
    sa, sb = {w[:5] for w in wa}, {w[:5] for w in wb}
    return sa <= sb or sb <= sa


def near_identical(promised, shipped):
    """-> the shipped item that stands in for `promised`, or None."""
    for grp in NEAR_IDENTICAL:
        if any(g in _key(promised) for g in grp):
            for s in shipped:
                if any(g in _key(s) for g in grp):
                    return s
    return None


SIBLINGS = {}


def promised_lists(base, headers, verbose=True):
    """-> {parent_sku: [promised items]} parsed from LIVE product descriptions.

    🔴 Parse the <li> ELEMENTS, not flattened text. Flattening turned one bullet into
    several "items": "KM39 - <em>caramelized Spanish grana style</em>" became KM39 AND
    "caramelized Spanish grana style", "(large only)" became its own item, and an <em>
    split mid-word produced 'C' + 'racked & Marinated Verdial Olives'. That reported 381
    substitutions across 71 of 76 orders -- all of them mine.

    🔴 Some limited releases describe CATEGORIES, not named items ("3 hand-selected
    artisan cheeses", "2 small-batch jams" -- MDT/MONG/SPN). Those cannot be checked item
    by item and are returned as None so the caller SKIPS them, rather than reporting every
    one of their orders as missing everything.
    """
    d = shopify_graphql(base, headers, PRODUCTS, {"q": "sku:AHB-X*"})
    out = {}
    for p in d["products"]["nodes"]:
        h = p["descriptionHtml"] or ""
        # TWO description shapes in use: <ul><li> bullets, and bullet CHARACTERS separated
        # by <br> inside a <p>. Handling only the first left 17 of 25 SKUs "unparsable".
        chunks = re.findall(r"<li[^>]*>(.*?)</li>", h, re.S | re.I)
        if not chunks:
            body = h[h.lower().find("included"):] if "included" in h.lower() else ""
            chunks = [c for c in re.split(r"<br\s*/?>", body, flags=re.I) if "•" in c]
        items = []
        for c in chunks:
            txt = re.sub(r"<em[^>]*>.*?</em>", "", c, flags=re.S | re.I)   # flavour clause
            txt = html.unescape(re.sub(r"<[^>]+>", " ", txt)).replace(" ", " ")
            txt = txt.split(" - ")[0].split(" — ")[0]
            txt = re.sub(r"\s+", " ", txt).strip(" •-–—	")
            # 🔴 the "(large only)" scope marker lives INSIDE the <em> that carries the
            # flavour clause -- capture it before the strip, or every medium box reports
            # the large-only cheese as missing.
            scope = VARIANT_ONLY.search(html.unescape(re.sub(r"<[^>]+>", " ", c)))
            if txt and len(txt) < 70 and not txt.lower().startswith(("limited", "available")):
                items.append(f"{txt} {scope.group(0)}" if scope else txt)
        # categorical when EVERY item leads with a count ("8 Curator's Choice Artisan
        # Cheeses", "3 hand-selected artisan cheeses") -- a promise of KIND, not of items.
        n_count = sum(bool(re.match(r"\d+\s", i)) for i in items)
        if items and (n_count == len(items) or n_count >= max(2, len(items) // 2)):
            items = None                       # categorical, not item-checkable
        sibs = [v["sku"] for v in p["variants"]["nodes"] if v["sku"]]
        for sk in sibs:
            out[sk] = items
            SIBLINGS[sk] = sibs
    if verbose:
        named = {k: v for k, v in out.items() if v}
        cat = sorted(k for k, v in out.items() if v is None)
        empty = sorted(k for k, v in out.items() if v == [])
        print(f"  {len(out)} limited-release SKUs: {len(named)} with a NAMED item list")
        if cat:
            print(f"    categorical (not item-checkable), skipped: {cat}")
        if empty:
            print(f"    🔴 no parsable <li> list: {empty}")
    return out


def run(since="2026-08-20", out_dir=None, verbose=True):
    base, headers = get_shopify_auth()
    promised = promised_lists(base, headers, verbose)
    after, orders = None, []
    while True:
        d = shopify_graphql(base, headers, ORDERS,
                            {"q": f"fulfillment_status:unfulfilled AND created_at:>{since}",
                             "after": after})["orders"]
        orders += d["nodes"]
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    # within one product, the variant carrying the SMALLER item count is not the large one
    size_by_count = {}
    for skus in SIBLINGS.values():
        nums = {sk: int(re.search(r"X(\d+)", sk).group(1)) for sk in skus if re.search(r"X(\d+)", sk)}
        if len(nums) > 1:
            hi = max(nums.values())
            for sk, n in nums.items():
                size_by_count[sk] = "large" if n == hi else "medium"
    rows, clean, unparsed = [], 0, 0
    for o in orders:
        live = [n for n in o["lineItems"]["nodes"] if (n.get("currentQuantity") or 0) > 0]
        parent = next((n["sku"] for n in live if (n["sku"] or "").startswith("AHB-X")), None)
        if not parent:
            continue
        want = promised.get(parent)
        if not want:
            unparsed += 1
            continue
        # 🔴 Size is encoded three ways across AHB-X*: an explicit -M/-L suffix, and the
        # ITEM-COUNT digit (AHB-X9MCHZ vs AHB-X11LCHZ). Reading only the suffix left
        # X9MCHZ with variant="" so "(large only)" items were required on the small box.
        variant = ("large" if "-L" in parent or "LCUST" in parent else
                   "medium" if "-M" in parent or "MCUST" in parent else
                   size_by_count.get(parent, ""))
        shipped = [n["title"] for n in live if (n["sku"] or "").startswith(CHILD)]
        miss = []
        for p in want:
            m = VARIANT_ONLY.search(p)
            if m and variant and m.group(1).lower() != variant:
                continue                              # not promised on this variant
            if not any(_same(p, s) for s in shipped):
                miss.append(p)
        if not miss:
            clean += 1
            continue
        for p in miss:
            sub = near_identical(p, shipped)
            rows.append({"Order ID": o["name"].lstrip("#"), "parent": parent,
                         "ship_tag": ",".join(t for t in o["tags"] if t.startswith("_SHIP_")),
                         "email": (o.get("customer") or {}).get("email", ""),
                         "promised_missing": VARIANT_ONLY.sub("", p).strip(),
                         "substitute": sub or "",
                         "class": "near-identical - no contact" if sub else "🔴 OUTREACH",
                         "shipped": "; ".join(shipped)})
    if verbose:
        print(f"  limited-release orders checked {clean + len({r['Order ID'] for r in rows})}"
              f" · clean {clean} · with a missing promised item {len({r['Order ID'] for r in rows})}"
              + (f" · 🔴 {unparsed} skipped, no parsable description" if unparsed else ""))
        for c in ("🔴 OUTREACH", "near-identical - no contact"):
            n = [r for r in rows if r["class"] == c]
            if n:
                print(f"    {c}: {len(n)} items on {len({r['Order ID'] for r in n})} orders")
    if out_dir and rows:
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "limited_release_substitutions.csv")
        with open(p, "w", newline="", encoding="utf8") as fh:
            w = csv.DictWriter(fh, list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"    -> {p} ({len(rows)})")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(prog="order_checks.limited_release")
    ap.add_argument("--since", default="2026-08-20")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    print("\n=== limited-release: shipped vs published description ===")
    run(a.since, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
