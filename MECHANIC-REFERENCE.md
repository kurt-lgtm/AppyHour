# Mechanic (Shopify Automation) — Reference for AI Agents

Hard-won patterns and gotchas for building Mechanic tasks that use Shopify's GraphQL Admin API, especially Order Editing.

---

## Subscriptions

- **`mechanic/user/order`** — Use this for manual triggers against a specific order. Gives you the order selection UI in Mechanic.
- **`mechanic/user/trigger`** — General-purpose manual trigger. Does NOT let you select an order. Don't use this if you need order context.
- **`mechanic/actions/perform`** — Fires after any action (shopify, cache, email, event) completes. Required for async two-step patterns.

## Liquid Basics

### Safe empty array initialization
```liquid
{% assign my_array = "" | split: "," %}
```
Do NOT use `{% assign my_array = array %}` — `array` is not a reliable global in Mechanic. If it resolves to nil, `push` silently fails and the array stays empty with no error.

### The `| shopify` filter
- **Only allows queries**, not mutations.
- Attempting a mutation will error: `Mutations are not allowed; use the 'shopify' action instead`
- Use `{% action "shopify" %}` for mutations.

### Permissions
Declare required permissions at the top of the task:
```liquid
{% permissions %}
  write_order_edits
{% endpermissions %}
```

---

## Actions and the Async Pattern

### The `{% action "shopify" %}` tag
This is the ONLY way to run mutations. It's async — the mutation runs, then `mechanic/actions/perform` fires with the result.

```liquid
{% action "shopify" %}
  mutation {
    orderEditBegin(id: "gid://shopify/Order/123") {
      calculatedOrder { id }
      userErrors { field message }
    }
  }
{% endaction %}
```

### Generic `{% action %}` JSON — DO NOT USE for shopify mutations
```liquid
{% comment %} THIS DOES NOT TRIGGER mechanic/actions/perform {% endcomment %}
{% action %}
  {
    "type": "shopify",
    "options": "mutation { ... }",
    "meta": { "order_id": 123 }
  }
{% endaction %}
```
The mutation will execute, but `mechanic/actions/perform` will NOT fire. You lose the ability to chain steps. Always use `{% action "shopify" %}` instead.

### `mechanic/actions/perform` event.data structure

When `mechanic/actions/perform` fires, the event data is structured as:

```json
{
  "type": "shopify",
  "options": "mutation { orderEditBegin(id: \"gid://shopify/Order/123\") { ... } }",
  "meta": null,
  "run": {
    "ok": true,
    "result": {
      "data": {
        "orderEditBegin": {
          "calculatedOrder": { "id": "gid://shopify/CalculatedOrder/456" },
          "userErrors": []
        }
      }
    }
  }
}
```

**CRITICAL paths:**
| What you want | Correct path | WRONG path (will be nil) |
|---|---|---|
| GraphQL result | `event.data.run.result` | `event.data.result` ❌ |
| Action type | `event.data.type` | — |
| Mutation string | `event.data.options` | — |

### Passing data across async steps

Since `{% action "shopify" %}` doesn't support meta, use one or both of:

1. **Cache** — Store context before the action, retrieve in `mechanic/actions/perform`:
```liquid
{% comment %} Step 2: Store and fire {% endcomment %}
{% action "cache", "set", "mykey_ORDER_ID", '{"count":2,"suffix":"OWC"}' %}
{% action "shopify" %}
  mutation { orderEditBegin(id: "gid://shopify/Order/ORDER_ID") { ... } }
{% endaction %}

{% comment %} Step 3: Retrieve {% endcomment %}
{% assign context = cache["mykey_ORDER_ID"] | parse_json %}
```

2. **Parse order ID from mutation string** — The mutation is in `event.data.options`:
```liquid
{% assign parts = event.data.options | split: "gid://shopify/Order/" %}
{% assign order_id = parts[1] | split: '"' | first %}
```

---

## Order Editing API — CalculatedOrder Gotchas

### CalculatedOrder ≠ Order
- `CalculatedOrder` has a **completely different numeric ID** than the original Order.
- Example: Order `6969428148504` → CalculatedOrder `121321455896`
- **There is NO `.order` field on CalculatedOrder.** Requesting it will cause a GraphQL error.

### CalculatedLineItem
- **There is NO `.originalLineItem` field.** Requesting it will cause a GraphQL error.
- Use `variant { id }` to match line items instead.

### Valid orderEditBegin query
```graphql
mutation {
  orderEditBegin(id: "gid://shopify/Order/123") {
    calculatedOrder {
      id
      lineItems(first: 50) {
        edges {
          node {
            id
            quantity
            variant {
              id
            }
          }
        }
      }
    }
    userErrors { field message }
  }
}
```

### INVALID fields (will cause "field doesn't exist" errors)
```graphql
# ❌ These do NOT exist:
calculatedOrder {
  order { id }                    # ❌ No .order on CalculatedOrder
  lineItems {
    edges {
      node {
        originalLineItem { id }   # ❌ No .originalLineItem on CalculatedLineItem
      }
    }
  }
}
```

### Zeroed-out (refunded) line items
When a line item is refunded to qty 0:
- It **still appears** in `calculatedOrder.lineItems` with `quantity: 0`
- `orderEditSetQuantity` **cannot restore it** — it will silently fail or error
- Use `orderEditAddVariant` with `allowDuplicates: true` instead — this creates a new line item alongside the dead one
- The dead line item (qty 0) will remain on the order but won't affect fulfillment

### Combining mutations
You can combine add/update + commit in a single action:
```liquid
{% action "shopify" %}
  mutation {
    orderEditAddVariant(
      id: {{ calc_order_id | json }},
      variantId: {{ variant_gid | json }},
      quantity: {{ qty }},
      allowDuplicates: true
    ) {
      calculatedOrder { id }
      userErrors { field message }
    }
    orderEditCommit(
      id: {{ calc_order_id | json }},
      notifyCustomer: false,
      staffNote: "Auto-added item"
    ) {
      order { id }
      userErrors { field message }
    }
  }
{% endaction %}
```

Note: `order { id }` IS valid on `orderEditCommit` response (it's `OrderEditCommitPayload.order`, not `CalculatedOrder.order`).

---

## Elevate Foods / AppyHour SKU Structure

- Suffixes: MONG, OWC, SPN, MDT, BYO, ALPN, SS, ISUN, HHIGH, MS, NMS
- Main box SKUs end with suffix: `AHB-LCUST-OWC`
- `CEX-EC` — Curator's Choice Artisan Cheese add-on (generic)
- `CEX-EC-[SUFFIX]` — Companion SKU matching box flavor (e.g. `CEX-EC-OWC`)
- `PR-CJAM-[SUFFIX]` — Bonus cheese & jam pairing
- Prefixes: `CH-` cheese, `MT-` meat, `AC-` accompaniment, `PK-` packaging
- Recharge bundles explode into individual line items on orders

### CEX-EC Companion Variant GIDs
```
MONG  → gid://shopify/ProductVariant/50601016885528
OWC   → gid://shopify/ProductVariant/50680144822552
SPN   → gid://shopify/ProductVariant/50680145346840
MDT   → gid://shopify/ProductVariant/50680148787480
BYO   → gid://shopify/ProductVariant/50680150524184
ALPN  → gid://shopify/ProductVariant/50824172765464
SS    → gid://shopify/ProductVariant/50834554683672
ISUN  → gid://shopify/ProductVariant/51012830363928
HHIGH → gid://shopify/ProductVariant/51474933350680
MS    → gid://shopify/ProductVariant/51650318008600
NMS   → gid://shopify/ProductVariant/51734918136088
```

---

## Debugging Mechanic Tasks

- **Liquid console** on each event page only has access to that event's context
- Check `event.data.type` to distinguish cache vs shopify action callbacks
- The `result.data.orderEditBegin` check naturally filters out non-shopify action callbacks (cache, email, event actions won't have that key)
- Add `{% log %}` statements liberally — they show up in the task run log
- Always check `userErrors` on every GraphQL response before proceeding
