# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Count CH-TETI units across orders, split by fulfillment status."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "InventoryReorder"))

from utils import get_shopify_auth, shopify_graphql

SKU = "CH-TETI"

QUERY = """
query($cursor: String) {
  orders(first: 100, after: $cursor, query: "sku:%s") {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        name
        displayFulfillmentStatus
        lineItems(first: 50) {
          edges { node { sku quantity } }
        }
      }
    }
  }
}
""" % SKU

def main():
    base, headers = get_shopify_auth()
    cursor = None
    ful_orders = ful_units = 0
    unf_orders = unf_units = 0
    statuses = {}
    while True:
        data = shopify_graphql(base, headers, QUERY, {"cursor": cursor}, resource="orders-live")
        conn = data["orders"]
        for edge in conn["edges"]:
            node = edge["node"]
            qty = sum(li["node"]["quantity"] for li in node["lineItems"]["edges"]
                      if li["node"]["sku"] == SKU)
            if qty == 0:
                continue
            st = node["displayFulfillmentStatus"]
            statuses[st] = statuses.get(st, 0) + 1
            if st == "FULFILLED":
                ful_orders += 1
                ful_units += qty
            else:
                unf_orders += 1
                unf_units += qty
        if conn["pageInfo"]["hasNextPage"]:
            cursor = conn["pageInfo"]["endCursor"]
        else:
            break

    print(f"SKU {SKU}")
    print(f"  FULFILLED:    {ful_orders} orders, {ful_units} units")
    print(f"  NOT fulfilled: {unf_orders} orders, {unf_units} units")
    print(f"  status breakdown: {statuses}")

if __name__ == "__main__":
    main()
