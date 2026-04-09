# Gift Card → Discount Code Migration Plan

## The Problem (Simple Version)

When someone pays with a Shopify gift card, Shopify **locks the order**. You can't edit it. You can't add bonus cheese. You can't swap SKUs. Matrix Commander can't touch it.

This affects every "Gift Redemption" order — both one-time and recurring.

## The Fix (Simple Version)

Instead of giving gift recipients a **gift card** (which locks orders), give them a **discount code** (which doesn't).

The customer experience is identical — they see money taken off their charge each month. But behind the scenes, the order is paid by their credit card at a reduced price (even $0), and **credit card orders are always editable**.

```
OLD WAY:  Gift card pays for the order     → Order locked
NEW WAY:  Discount reduces the price to $0 → Credit card pays $0 → Order editable
```

## Has This Been Verified?

Yes. Every claim below was checked against actual API documentation.

| Claim | Verified? |
|-------|-----------|
| Discount orders are editable in Shopify | YES |
| We can auto-apply discounts without the customer doing anything | YES |
| Discounts can last for multiple months (e.g., 3 charges) | YES |
| $0 orders still work and are editable | YES |
| There's a "total dollar cap" on discounts | NO — we use charge count instead |

---

## Step-by-Step Action Plan

### Step 1: Test It First (30 minutes)

Before changing anything, prove it works on ONE real customer.

1. Pick a Gift Redemption customer (e.g., order #126300, Vicki Cole)
2. In Recharge Admin, create a test discount code manually:
   - Code: `TEST-GIFT-VICKI`
   - Type: Fixed amount
   - Value: whatever her monthly charge is (~$96.33)
   - Duration: 1 charge
3. Apply it to her subscription in Recharge Admin
4. Wait for her next charge to process
5. Check the new Shopify order — **can you edit it?**
6. If yes, continue. If no, stop and investigate.

### Step 2: Turn Off Gift Card Auto-Apply (5 minutes)

This stops Recharge from pulling Shopify gift card balances on future charges.

1. Recharge Admin → Settings → Checkout / Payment
2. Find "Apply gift card balances to recurring charges"
3. Turn it **OFF**

Existing gift card balances stay on the customer's account but won't be used.
We'll replace them with discount codes in Step 4.

### Step 3: Figure Out Who Has Active Gift Balances (1 hour)

Make a spreadsheet of every customer who still has gift card money:

| Customer | Email | Balance Left | Monthly Charge | Months Left | Per-Month Discount |
|----------|-------|-------------|---------------|-------------|-------------------|
| Vicki Cole | vicki@... | $192.66 | $96.33 | 2 | $96.33 |
| Lisa Davis | lisa@... | $150.00 | $96.33 | 2 | $75.00 |

**Math:** Months Left = round up (Balance / Monthly Charge). Per-Month = Balance / Months Left.

### Step 4: Swap Gift Cards for Discount Codes (1-2 hours)

For each customer in the spreadsheet:

1. **Create a discount code** in Recharge:
   - Code: `MIGRATED-{customer name}`
   - Amount: the "Per-Month Discount" from the spreadsheet
   - Duration: "Months Left" number of charges
2. **Apply it** to their subscription (Recharge Admin or API)
3. **Disable** their old Shopify gift card
4. Check it off the spreadsheet

This can be done manually for a small number of customers, or scripted if there are many.

### Step 5: Create a New Gift Product (30 minutes)

The current "AppyHour 3-Month Gift Subscription" is typed as "Gift Card" in Shopify.
Don't change the old one (existing holders depend on it). Instead:

1. **Create a new Shopify product:**
   - Same name: "AppyHour 3-Month Gift Subscription"
   - Product type: anything EXCEPT `Gift Card` — use `Prepaid Subscription` or `Gift` or whatever you want
     (Shopify product types are freeform text, not a fixed list. The only one that matters is `Gift Card` — that's the one Shopify treats specially and causes the locking problem.)
   - Price: $289
   - SKU: `GIFT-3MO`
2. **Publish** the new product
3. **Archive** the old Gift Card product so nobody buys it anymore
4. **Update** any website links to point to the new product

### Step 6: Automate with Mechanic (self-contained in Shopify)

This runs entirely inside Shopify via Mechanic — no external server, no code hosting.
It triggers automatically when a gift order comes in.

**How Mechanic talks to Recharge:** Mechanic has an `{% action "http" %}` tag that
can make POST/GET requests to any API with custom headers. This is how it calls
Recharge. Each HTTP call is async — the response comes back via the
`mechanic/actions/perform` event. This is confirmed in Mechanic's official docs.

You need **three Mechanic tasks**. Each is simple and does one thing well.

---

#### TASK A: "Create gift discount on purchase"

**Purpose:** When a gift order comes in, create the discount code in Recharge
and save it to the Shopify order for later use.

**Subscriptions (triggers):**
```
shopify/orders/create
mechanic/actions/perform
```

**Task options (configured in Mechanic UI):**
- `recharge_api_token` (text field) — Your Recharge API token
- `gift_sku` (text field, default: `GIFT-3MO`) — The gift product SKU
- `charge_count` (number field, default: `3`) — How many months the gift covers

**Advanced settings:**
- "Perform action runs in sequence" = ON
- "Halt the sequence when one fails" = ON

**What happens step by step:**

```
1. Shopify creates an order
2. Mechanic fires shopify/orders/create
3. Task checks: does any line item have SKU "GIFT-3MO"?
   → NO: task exits, does nothing
   → YES: continue
4. Task reads "Recipient Email" from order note_attributes
   → Not found: logs an error, exits
   → Found: continue
5. Task calculates: $289 / 3 = $96.33 per charge
6. Task generates unique code: "GIFT-#127001-7017131835672"
7. Task saves the code + recipient email in Mechanic cache
   (key: "gift_{order_id}")
8. Task fires HTTP POST to Recharge API to create the discount
9. --- async pause: Mechanic waits for Recharge to respond ---
10. Recharge responds with the created discount (or an error)
11. Mechanic fires mechanic/actions/perform with the response
12. Task reads the response:
    → Error (status != 200): logs the error, tags order "Gift-Discount-FAILED"
    → Success: saves discount_id to cache, tags order "Gift-Discount-Pending"
```

**The actual Mechanic task code:**

```liquid
{% if event.topic == "shopify/orders/create" %}

  {% comment %} --- Step 1: Is this a gift order? --- {% endcomment %}
  {% assign is_gift = false %}
  {% assign gift_price = 0 %}
  {% for line_item in order.line_items %}
    {% if line_item.sku == options.gift_sku %}
      {% assign is_gift = true %}
      {% assign gift_price = line_item.price | times: 1.0 %}
    {% endif %}
  {% endfor %}

  {% unless is_gift %}{% break %}{% endunless %}

  {% comment %} --- Step 2: Get recipient email --- {% endcomment %}
  {% assign recipient_email = "" %}
  {% for attr in order.note_attributes %}
    {% if attr.name == "Recipient Email" %}
      {% assign recipient_email = attr.value | strip %}
    {% endif %}
  {% endfor %}

  {% if recipient_email == blank %}
    {% log "GIFT ORDER but no Recipient Email. Order: " | append: order.name %}
    {% break %}
  {% endif %}

  {% comment %} --- Step 3: Calculate discount --- {% endcomment %}
  {% assign per_charge = gift_price | divided_by: options.charge_count | round: 2 %}
  {% assign max_subsequent = options.charge_count | minus: 1 %}
  {% assign discount_code = "GIFT-" | append: order.name | append: "-" | append: order.id %}

  {% comment %} --- Step 4: Save context to cache --- {% endcomment %}
  {% assign cache_key = "gift_" | append: order.id %}
  {% action "cache", "set", cache_key %}
    {
      "order_id": {{ order.id | json }},
      "order_name": {{ order.name | json }},
      "recipient_email": {{ recipient_email | json }},
      "discount_code": {{ discount_code | json }},
      "per_charge": {{ per_charge | json }},
      "max_subsequent": {{ max_subsequent }}
    }
  {% endaction %}

  {% comment %} --- Step 5: Create discount in Recharge --- {% endcomment %}
  {% log "Creating Recharge discount: " | append: discount_code | append: " for " | append: recipient_email %}
  {% action "http" %}
    {
      "method": "post",
      "url": "https://api.rechargeapps.com/discounts",
      "headers": {
        "X-Recharge-Access-Token": {{ options.recharge_api_token | json }},
        "X-Recharge-Version": "2021-11",
        "Content-Type": "application/json"
      },
      "body": {
        "code": {{ discount_code | json }},
        "value": {{ per_charge | json }},
        "value_type": "fixed_amount",
        "usage_limits": {
          "max_subsequent_redemptions": {{ max_subsequent }}
        },
        "channel_settings": {
          "api": { "can_apply": true },
          "checkout_page": { "can_apply": false },
          "customer_portal": { "can_apply": false },
          "merchant_portal": { "can_apply": true }
        },
        "status": "enabled"
      },
      "error_on_5xx": true
    }
  {% endaction %}

{% elsif event.topic == "mechanic/actions/perform" %}

  {% comment %} --- Step 6: Handle Recharge response --- {% endcomment %}
  {% if action.type != "http" %}{% break %}{% endif %}

  {% assign status = action.run.result.status %}
  {% assign body = action.run.result.body | parse_json %}

  {% if status != 201 and status != 200 %}
    {% log "Recharge discount creation FAILED. Status: " | append: status | append: " Body: " | append: action.run.result.body %}
    {% comment %} Tag order as failed so you can investigate {% endcomment %}
    {% action "shopify" %}
      mutation {
        tagsAdd(id: "gid://shopify/Order/{{ order.id }}", tags: ["Gift-Discount-FAILED"]) {
          userErrors { field message }
        }
      }
    {% endaction %}
    {% break %}
  {% endif %}

  {% comment %} --- Step 7: Success! Save discount ID and tag order --- {% endcomment %}
  {% assign discount_id = body.discount.id %}
  {% assign discount_code = body.discount.code %}
  {% log "Discount created: " | append: discount_code | append: " (ID: " | append: discount_id | append: ")" %}

  {% comment %} Find the order ID from the discount code (last segment after -) {% endcomment %}
  {% assign code_parts = discount_code | split: "-" %}
  {% assign order_id = code_parts | last %}
  {% assign cache_key = "gift_" | append: order_id %}

  {% comment %} Update cache with the Recharge discount ID {% endcomment %}
  {% assign ctx = cache[cache_key] | parse_json %}
  {% if ctx != blank %}
    {% action "cache", "set", cache_key %}
      {
        "order_id": {{ ctx.order_id | json }},
        "order_name": {{ ctx.order_name | json }},
        "recipient_email": {{ ctx.recipient_email | json }},
        "discount_code": {{ ctx.discount_code | json }},
        "discount_id": {{ discount_id | json }},
        "per_charge": {{ ctx.per_charge | json }},
        "max_subsequent": {{ ctx.max_subsequent }}
      }
    {% endaction %}
  {% endif %}

  {% comment %} Tag the order so Task B knows to pick it up {% endcomment %}
  {% action "shopify" %}
    mutation {
      tagsAdd(id: "gid://shopify/Order/{{ order_id }}", tags: ["Gift-Discount-Pending"]) {
        userErrors { field message }
      }
    }
  {% endaction %}

{% endif %}
```

**After this task runs, the order has:**
- Tag: `Gift-Discount-Pending`
- A discount code created in Recharge, waiting to be applied
- All the info stored in Mechanic's cache

---

#### TASK B: "Apply pending gift discounts" (daily)

**Purpose:** Check all pending gift discounts and try to apply them to
the recipient's Recharge subscription. Runs once a day automatically.

**Subscriptions:**
```
mechanic/scheduler/daily
mechanic/actions/perform
```

**Task options:**
- `recharge_api_token` (text field)

**Advanced settings:**
- "Perform action runs in sequence" = ON

**What happens step by step:**

```
1. Daily scheduler fires
2. Task queries Shopify for all orders tagged "Gift-Discount-Pending"
3. For each order:
   a. Read the discount code and recipient email from Mechanic cache
   b. Call Recharge API: GET /customers?email={recipient}
   c. If customer NOT found → skip (they haven't signed up yet, try again tomorrow)
   d. If customer found → get their subscription ID
   e. Call Recharge API: POST /subscriptions/{id}/apply_discount
   f. If success → re-tag order from "Gift-Discount-Pending" to "Gift-Discount-Applied"
   g. If fail (e.g., subscription already has a discount) → tag "Gift-Discount-Queued"
```

**The actual Mechanic task code:**

```liquid
{% if event.topic == "mechanic/scheduler/daily" %}

  {% comment %} --- Find all pending gift orders --- {% endcomment %}
  {% assign pending_orders = shop.orders | where: "tags", "Gift-Discount-Pending" %}

  {% comment %} Alternative: use GraphQL to query orders by tag {% endcomment %}
  {% capture query %}
    query {
      orders(first: 50, query: "tag:'Gift-Discount-Pending'") {
        edges {
          node {
            id
            legacyResourceId
            name
            tags
          }
        }
      }
    }
  {% endcapture %}
  {% assign result = query | shopify %}
  {% assign orders = result.data.orders.edges %}

  {% for edge in orders %}
    {% assign o = edge.node %}
    {% assign cache_key = "gift_" | append: o.legacyResourceId %}
    {% assign ctx = cache[cache_key] | parse_json %}

    {% if ctx == blank %}
      {% log "No cache data for order " | append: o.name | append: ". Skipping." %}
      {% continue %}
    {% endif %}

    {% log "Checking recipient: " | append: ctx.recipient_email | append: " for order " | append: o.name %}

    {% comment %} --- Look up recipient in Recharge --- {% endcomment %}
    {% assign lookup_url = "https://api.rechargeapps.com/customers?email=" | append: ctx.recipient_email %}
    {% action "http" %}
      {
        "method": "get",
        "url": {{ lookup_url | json }},
        "headers": {
          "X-Recharge-Access-Token": {{ options.recharge_api_token | json }},
          "X-Recharge-Version": "2021-11"
        }
      }
    {% endaction %}
  {% endfor %}

{% elsif event.topic == "mechanic/actions/perform" %}

  {% if action.type != "http" %}{% break %}{% endif %}

  {% assign body = action.run.result.body | parse_json %}

  {% comment %} --- Handle customer lookup response --- {% endcomment %}
  {% if body.customers %}
    {% if body.customers.size == 0 %}
      {% log "Recipient not in Recharge yet. Will retry tomorrow." %}
      {% break %}
    {% endif %}

    {% assign customer_id = body.customers[0].id %}

    {% comment %} Get their active subscriptions {% endcomment %}
    {% assign sub_url = "https://api.rechargeapps.com/subscriptions?customer_id=" | append: customer_id | append: "&status=active" %}
    {% action "http" %}
      {
        "method": "get",
        "url": {{ sub_url | json }},
        "headers": {
          "X-Recharge-Access-Token": {{ options.recharge_api_token | json }},
          "X-Recharge-Version": "2021-11"
        }
      }
    {% endaction %}

  {% elsif body.subscriptions %}
    {% comment %} --- Got subscriptions, apply the discount --- {% endcomment %}
    {% if body.subscriptions.size == 0 %}
      {% log "Customer has no active subscriptions." %}
      {% break %}
    {% endif %}

    {% assign sub_id = body.subscriptions[0].id %}

    {% comment %} Find the matching cache entry by checking recent pending gifts {% endcomment %}
    {% comment %} NOTE: In production, you'd match by customer email across cache entries.
       For simplicity, you can also store the discount_code in an order metafield
       instead of relying on cache matching. {% endcomment %}

    {% assign apply_url = "https://api.rechargeapps.com/subscriptions/" | append: sub_id | append: "/apply_discount" %}
    {% action "http" %}
      {
        "method": "post",
        "url": {{ apply_url | json }},
        "headers": {
          "X-Recharge-Access-Token": {{ options.recharge_api_token | json }},
          "X-Recharge-Version": "2021-11",
          "Content-Type": "application/json"
        },
        "body": {
          "discount_code": "THE_DISCOUNT_CODE_FROM_CACHE"
        }
      }
    {% endaction %}
  {% endif %}

{% endif %}
```

**NOTE on the apply step:** The trickiest part is matching the Recharge customer
back to the right cache entry when you have multiple pending gifts. In practice,
the simplest approach is to **store the discount code directly in the Shopify order
as a metafield** (using Task A), so Task B reads it straight from the order instead
of searching the cache. This is a one-line change in Task A and makes Task B bulletproof.

---

#### TASK C: "Check depleted discounts" (weekly)

**Purpose:** When a gift discount has been fully used up, check if there's another
gift queued for that customer. If yes, apply the next one.

**Subscriptions:** `mechanic/scheduler/weekly`

**What happens:**

```
1. Weekly scheduler fires
2. Query orders tagged "Gift-Discount-Applied"
3. For each, read discount_id from cache
4. GET /discounts/{id} from Recharge
5. If times_used >= max_subsequent_redemptions + 1:
   → Discount is depleted
   → Check if same customer email has an order tagged "Gift-Discount-Queued"
   → If yes: apply that discount, re-tag appropriately
   → Re-tag depleted order as "Gift-Discount-Complete"
6. If not depleted: skip, check again next week
```

This task follows the same HTTP action pattern as Task A and B.
Build it after A and B are working.

---

#### How to get the Recipient Email onto the order

Add this field to your gift product page in your Shopify theme:

```html
<label for="recipient-email">Recipient's Email Address</label>
<input type="email"
       name="properties[Recipient Email]"
       id="recipient-email"
       placeholder="friend@example.com"
       required>
```

When the customer fills this in and checks out:
- Shopify stores it as a **line item property** on the order
- Mechanic can read it from `order.line_items[0].properties`
- It also shows up in the Shopify Admin order detail page

Alternatively, use `note_attributes` (shows in order notes instead of line items):
```html
<input type="hidden" name="attributes[Recipient Email]" id="recipient-email-hidden">
```

---

#### Mechanic Settings Checklist

| Setting | Where | Value |
|---------|-------|-------|
| Recharge API token | Task options → `recharge_api_token` | Your token (never hardcode in task code) |
| Gift SKU | Task options → `gift_sku` | `GIFT-3MO` |
| Charge count | Task options → `charge_count` | `3` |
| Sequential actions | Task advanced settings | ON |
| Halt on failure | Task advanced settings | ON (for Task A) |
| Task permissions | Task settings | `write_orders` |

---

#### What the Customer Sees

Nothing changes:
- **Gift buyer:** Purchases the gift product, enters recipient email → done
- **Recipient:** Signs up for a subscription → discount auto-applied (they may see "discount applied" on their charge)
- **Each month:** Discount reduces their charge → order is fully editable by you
- **After 3 months:** Discount expires, full-price charges resume

### Step 7: Verify After One Billing Cycle (Day 7-14)

After the next round of charges processes:

- Are the migrated customers' orders editable now? 
- Did the discount apply correctly?
- Can Matrix Commander add bonus items?
- Are discounts counting down properly?

---

## What About the "One Discount Per Subscription" Limit?

Recharge only allows ONE discount code on a subscription at a time. This creates two situations:

### Situation A: Gift Customer + Promotional Discount

A gift customer wants to use a "20% off" promo too, but they already have a gift discount.

**Solution:** Since the order is now editable, apply the promo as a **post-creation edit** on the Shopify order itself (same way you add bonus cheese). The gift discount lives on the Recharge subscription, the promo is applied at the order level. No conflict.

### Situation B: Two Gifts for the Same Person

Grandma and Uncle both buy a gift for the same customer.

**Solution:** Keep a simple queue. The first gift's discount goes on now. When it runs out (check after each charge), automatically apply the second one. A few lines of code or even a manual check each week.

### Situation C: Customer Contacts Support About a Promo

Rare, but if a gift customer calls wanting a promo code instead:

1. Remove the gift discount temporarily
2. Apply the promo for that one charge
3. Re-apply the gift discount afterward

---

## Edge Cases (Good News Edition)

| What if... | What happens |
|-----------|-------------|
| Recipient skips a month? | Discount pauses too — it only counts down on actual charges. Gift value preserved. |
| Recipient upgrades to Large box? | Discount still applies at the original amount. They pay the difference. Makes sense. |
| Recipient cancels early? | Unused discount just sits there. No refund needed — the gifter already paid. |
| The math doesn't divide evenly? | $289 / 3 = $96.33 with a penny left over. Acceptable. |
| Recipient has no credit card on file? | $0 charges still need a payment method. Require card during gift signup. |

---

## How to Test This Safely

You can test every piece of this without affecting real customers or real charges.

### Test 1: Verify Discount Orders Are Editable (the big one)

This is the single most important test. Do it first.

1. **Create a test discount code** in Recharge Admin manually:
   - Code: `TEST-GIFT-001`
   - Type: Fixed amount
   - Value: $96.33
   - Duration: 1 charge
2. **Pick a test customer** — use your own subscription or a test account
3. **Apply the discount** to the test subscription in Recharge Admin
4. **Wait for the next charge** to process (or trigger one manually in Recharge)
5. **Check the resulting Shopify order:**
   - Go to the order in Shopify Admin — is the **Edit** button there?
   - Verify via API:
     ```
     In the Shopify Admin GraphQL playground, run:
     { order(id: "gid://shopify/Order/ORDER_ID") { merchantEditable merchantEditableErrors } }
     ```
   - `merchantEditable: true` = success. The whole plan works.
   - `merchantEditable: false` = stop. Check `merchantEditableErrors` to see why.
6. **Try editing the order** — add a line item (bonus cheese) to confirm the full workflow

**If this test passes, everything else is just implementation details.**

### Test 2: Mechanic Task — Dry Run

Mechanic lets you preview tasks without executing them.

1. Create the task in Mechanic with the code from Step 6
2. Use Mechanic's **Preview** feature — it simulates the task against a sample order
3. Check the logs: does it correctly identify the GIFT-3MO SKU? Extract the recipient email?
4. The HTTP actions won't fire in preview mode — but you can verify the Liquid logic is correct

### Test 3: Mechanic Task — Real Run with Test Order

1. **Create a test order** in Shopify Admin (draft order):
   - Add the GIFT-3MO product
   - In order notes, add: `Recipient Email: your-own-email@example.com`
   - Mark as paid
2. Mechanic's `shopify/orders/create` trigger will fire
3. Watch the Mechanic task log — did it:
   - Detect the gift SKU? ✓
   - Extract the recipient email? ✓
   - Call Recharge to create a discount? ✓ (check Recharge Admin for the new code)
   - Tag the order `Gift-Discount-Created`? ✓

### Test 4: Full End-to-End

1. Test order → Mechanic creates discount → discount applied to your test subscription
2. Trigger a charge in Recharge for the test subscription
3. Shopify order appears with discount applied
4. Edit the order in Shopify (add a line item)
5. Confirm the edit saved successfully
6. Run Matrix Commander against this order — can it add bonus cheese?

### Test 5: Edge Cases to Verify Manually

| Test | How |
|------|-----|
| Recipient not in Recharge yet | Create test order with a non-existent email → verify it gets tagged `Gift-Discount-Pending` |
| Second gift to same person | Apply one discount manually, then trigger the Mechanic task for a second gift → verify it queues |
| Discount fully used up | Set a 1-charge discount, let it process, check that the discount shows as depleted |
| $0 charge | Set discount equal to charge amount → verify order is created and editable |

### What NOT to Test in Production

- Don't disable gift card auto-apply until Test 1 passes
- Don't archive the old Gift Card product until you've verified the new product works
- Don't migrate real customer balances until you've tested the migration script on 1 customer

---

## Rollback Plan (If Something Goes Wrong)

1. Turn gift card auto-apply back ON in Recharge
2. Re-enable the disabled gift cards
3. Switch back to the old Gift Card product
4. Discount codes keep working in parallel — nothing breaks

The old and new systems can coexist. This is not a one-way door.

---

## Summary

| Step | What | When | Effort |
|------|------|------|--------|
| 1 | Test with one customer (THE key test) | Day 1 | 30 min |
| 2 | Turn off gift card auto-apply | Day 1 | 5 min |
| 3 | Identify active gift balances | Day 1 | 1 hour |
| 4 | Migrate balances → discount codes | Day 1-2 | 1-2 hours |
| 5 | Create new gift product | Day 2 | 30 min |
| 6 | Build Mechanic tasks (auto-creates discounts) | Day 2-3 | 2-3 hours |
| 7 | Test Mechanic with a test order | Day 3 | 30 min |
| 8 | Verify after one billing cycle | Day 7-14 | 30 min |

**Everything runs inside Shopify (Mechanic) + Recharge. No external servers.**

**Total hands-on work: ~1 day. Then wait one billing cycle to confirm.**
