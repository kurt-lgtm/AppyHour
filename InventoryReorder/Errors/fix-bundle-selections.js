/**
 * fix-bundle-selections.js
 *
 * Updates Recharge bundle selection fallback items for subscriptions
 * affected by the API bug (dan@elevatefoods.co wrong fallback).
 *
 * Modes:
 *   --test <subscription_id>       Dry-run for ONE subscription (shows what would change)
 *   --test-live <subscription_id>  Actually update ONE subscription
 *   --dry-run                      Process ALL, log what would change, DON'T update
 *   --live                         Process ALL, actually update
 *
 * Usage:
 *   node fix-bundle-selections.js --test 622494224
 *   node fix-bundle-selections.js --test-live 622494224
 *   node fix-bundle-selections.js --dry-run
 *   node fix-bundle-selections.js --live
 */

const fs = require('fs');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

// ============================================================
// Configuration
// ============================================================
const INPUT_CSV = path.join(__dirname, 'final', 'AHB-LCUST-MDT-full-replace-correct-default.csv');
const OUTPUT_DIR = path.join(__dirname, 'output');
const TIMESTAMP = new Date().toISOString().slice(0, 10);

const RESULTS_CSV = path.join(OUTPUT_DIR, `bundle-fix-RESULTS-${TIMESTAMP}.csv`);
const BACKUP_CSV = path.join(OUTPUT_DIR, `bundle-fix-BACKUP-${TIMESTAMP}.csv`);
const ERROR_LOG = path.join(OUTPUT_DIR, `bundle-fix-ERRORS-${TIMESTAMP}.log`);
const CHECKPOINT_FILE = path.join(OUTPUT_DIR, `bundle-fix-CHECKPOINT-${TIMESTAMP}.json`);

const DELAY_MS = 350;
const MAX_RETRIES = 5;
const INITIAL_BACKOFF_MS = 1000;

// Recharge token rotation
const RC_TOKENS = [
  process.env.RECHARGE_API_TOKEN,
  process.env.RECHARGE_API_TOKEN_2,
  process.env.RECHARGE_API_TOKEN_3,
].filter(Boolean);
let rcTokenIdx = 0;
function getRcToken() {
  const t = RC_TOKENS[rcTokenIdx % RC_TOKENS.length];
  rcTokenIdx++;
  return t;
}

// ============================================================
// Logging
// ============================================================
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
}

function logError(msg) {
  const line = `[${new Date().toISOString()}] ERROR: ${msg}`;
  console.error(line);
  fs.appendFileSync(ERROR_LOG, line + '\n');
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ============================================================
// CSV Parser
// ============================================================
function parseCSV(text) {
  const rows = []; let current = []; let field = ''; let inQ = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQ) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') inQ = false;
      else field += ch;
    } else {
      if (ch === '"') inQ = true;
      else if (ch === ',') { current.push(field); field = ''; }
      else if (ch === '\n' || (ch === '\r' && text[i + 1] === '\n')) {
        if (ch === '\r') i++;
        current.push(field); field = '';
        if (current.length > 1 || current[0] !== '') rows.push(current);
        current = [];
      } else field += ch;
    }
  }
  if (field || current.length) { current.push(field); rows.push(current); }
  return rows;
}

function escapeCSV(val) {
  const s = String(val || '');
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

// ============================================================
// Recharge API (with retry + exponential backoff)
// ============================================================
async function rcRequest(method, endpoint, body) {
  let lastError;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const token = getRcToken();
    const backoffMs = INITIAL_BACKOFF_MS * Math.pow(2, attempt);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    try {
      const options = {
        method,
        headers: {
          'X-Recharge-Access-Token': token,
          'X-Recharge-Version': '2021-11',
          'Accept': 'application/json',
          'Content-Type': 'application/json',
        },
        signal: controller.signal,
      };

      if (body && (method === 'PUT' || method === 'POST')) {
        options.body = JSON.stringify(body);
      }

      const res = await fetch(`https://api.rechargeapps.com${endpoint}`, options);
      clearTimeout(timeoutId);

      if (res.status === 429) {
        const retryAfter = parseInt(res.headers.get('retry-after') || '5', 10);
        log(`  Rate limited (429), waiting ${retryAfter}s (attempt ${attempt + 1}/${MAX_RETRIES})`);
        await sleep(retryAfter * 1000);
        continue;
      }

      if (res.status >= 500) {
        const errBody = await res.text();
        lastError = new Error(`HTTP ${res.status}: ${errBody.slice(0, 300)}`);
        log(`  Server error ${res.status}, retrying in ${backoffMs}ms (attempt ${attempt + 1}/${MAX_RETRIES})`);
        await sleep(backoffMs);
        continue;
      }

      if (res.status >= 400) {
        const errBody = await res.text();
        throw new Error(`HTTP ${res.status}: ${errBody.slice(0, 500)}`);
      }

      return await res.json();
    } catch (e) {
      clearTimeout(timeoutId);
      lastError = e;

      if (e.name === 'AbortError' || e.code === 'ECONNRESET' || e.code === 'ETIMEDOUT') {
        log(`  Network error (${e.message}), retrying in ${backoffMs}ms (attempt ${attempt + 1}/${MAX_RETRIES})`);
        await sleep(backoffMs);
        continue;
      }

      throw e;
    }
  }

  throw lastError || new Error('Max retries exceeded');
}

async function rcGet(endpoint) { return rcRequest('GET', endpoint); }
async function rcPut(endpoint, body) { return rcRequest('PUT', endpoint, body); }

// ============================================================
// Parse fix_item_ids string -> array of { external_variant_id, quantity }
// Format: "1x 50044204122392 | 1x 49805282967832"
// ============================================================
function parseFixItemIds(fixItemIdsStr) {
  if (!fixItemIdsStr || !fixItemIdsStr.trim()) return [];

  return fixItemIdsStr.split('|').map(part => {
    const trimmed = part.trim();
    const match = trimmed.match(/^(\d+)x\s+(\d+)$/);
    if (!match) return null;
    return {
      quantity: parseInt(match[1]),
      external_variant_id: match[2],
    };
  }).filter(Boolean);
}

// ============================================================
// Find the correct bundle selection to update
// ============================================================
async function findBundleSelection(subscriptionId) {
  // purchase_item_id = subscription_id (confirmed pattern from fix-size-subs.js)
  const purchaseItemId = parseInt(subscriptionId);

  // Query ONLY bundle selections for this subscription's purchase_item_id
  const resp = await rcGet(`/bundle_selections?purchase_item_ids=${subscriptionId}`);
  const selections = resp.bundle_selections || [];
  const upcoming = selections.filter(s => s.charge_id === null);

  if (upcoming.length > 0) {
    // All results are already filtered to this purchase_item_id — use the first upcoming one
    const exact = upcoming[0];
    log(`  Found bundle selection ${exact.id} (purchase_item=${exact.purchase_item_id}, items=${exact.items?.length})`);
    return { selection: exact, allSelections: selections, purchaseItemId };
  }

  // No existing selection for this subscription — will POST to create one
  log(`  No bundle selection for purchase_item_id ${purchaseItemId} — will CREATE new one`);
  return { selection: null, allSelections: selections, purchaseItemId };
}

// ============================================================
// Compare current vs fix items
// ============================================================
function compareItems(currentItems, fixItems) {
  // Build qty maps: variant_id -> total quantity
  const currentMap = new Map();
  for (const i of (currentItems || [])) {
    const key = String(i.external_variant_id);
    currentMap.set(key, (currentMap.get(key) || 0) + (i.quantity || 1));
  }
  const fixMap = new Map();
  for (const i of fixItems) {
    const key = String(i.external_variant_id);
    fixMap.set(key, (fixMap.get(key) || 0) + i.quantity);
  }

  const currentTotal = [...currentMap.values()].reduce((s, v) => s + v, 0);
  const fixTotal = [...fixMap.values()].reduce((s, v) => s + v, 0);

  // Count matching items (min qty per variant)
  let matchCount = 0;
  for (const [id, qty] of fixMap) {
    matchCount += Math.min(qty, currentMap.get(id) || 0);
  }

  const total = Math.max(currentTotal, fixTotal, 1);
  const matchPct = Math.round((matchCount / total) * 100);
  const alreadyCorrect = matchPct === 100 && currentTotal === fixTotal;

  return { matchPct, alreadyCorrect, currentCount: currentTotal, fixCount: fixTotal };
}

// ============================================================
// Process a single subscription
// ============================================================
async function processSubscription(row, mode, allRows) {
  const { subscription_id, customer_id, email, sku, fix_type, fix_item_ids } = row;

  const result = {
    subscription_id,
    customer_id,
    email,
    sku,
    fix_type,
    bundle_selection_id: '',
    status: 'PENDING',
    message: '',
    current_items_before: '',
    fix_items_applied: '',
    current_items_after: '',
    match_before: 0,
    match_after: 0,
  };

  const isLive = mode === 'live' || mode === 'test-live';

  try {
    // 1. Parse and validate fix_item_ids
    const fixItems = parseFixItemIds(fix_item_ids);
    if (fixItems.length === 0) {
      result.status = 'SKIPPED';
      result.message = 'Empty fix_item_ids';
      return result;
    }

    // 2. Validate item count (7 for M, 9 for L, 6 for TRAY)
    const totalQty = fixItems.reduce((sum, i) => sum + i.quantity, 0);
    const isTray = (sku || '').includes('MCUR-TRAY');
    const isLarge = (sku || '').includes('LCUST') || (sku || '').includes('LCUR') || (sku || '').includes('-L');
    const expectedCount = isTray ? 6 : isLarge ? 9 : 7;
    if (totalQty !== expectedCount) {
      log(`  WARNING: fix items total qty ${totalQty} !== expected ${expectedCount} for SKU ${sku}`);
      result.message = `Item count mismatch: ${totalQty} vs expected ${expectedCount}. `;
    }

    // 3. Verify subscription is still active
    log(`  Checking subscription ${subscription_id}...`);
    const subResp = await rcGet(`/subscriptions/${subscription_id}`);
    const sub = subResp.subscription;
    await sleep(DELAY_MS);

    if (!sub) {
      result.status = 'SKIPPED';
      result.message = 'Subscription not found';
      return result;
    }

    if (sub.status !== 'active') {
      result.status = 'SKIPPED';
      result.message = `Subscription status: ${sub.status}`;
      return result;
    }

    // 4. Find bundle selection to update
    log(`  Finding bundle selection...`);
    const { selection, allSelections, purchaseItemId } = await findBundleSelection(subscription_id);
    await sleep(DELAY_MS);

    const needsCreate = !selection && purchaseItemId;

    if (!selection && !needsCreate) {
      result.status = 'ERROR';
      result.message = `No bundle selection found and no purchase_item_id from charge (${allSelections.length} total)`;
      return result;
    }

    if (selection) {
      result.bundle_selection_id = selection.id;
      log(`  Bundle selection ID: ${selection.id} (purchase_item: ${selection.purchase_item_id}, items: ${selection.items_count})`);
    } else {
      log(`  No matching bundle selection — will CREATE for purchase_item_id ${purchaseItemId}`);
    }

    // 5. Record current state (backup)
    const currentItems = selection ? (selection.items || []) : [];
    result.current_items_before = currentItems.map(i =>
      `${i.quantity}x ${i.external_variant_id}`
    ).join(' | ');

    // 6. Compare current vs fix
    const comparison = compareItems(currentItems, fixItems);
    result.match_before = comparison.matchPct;

    if (comparison.alreadyCorrect) {
      result.status = 'ALREADY_CORRECT';
      result.message = `Items already match (${comparison.matchPct}%)`;
      log(`  Already correct - skipping`);
      return result;
    }

    log(`  Current match: ${comparison.matchPct}% (${comparison.currentCount} current vs ${comparison.fixCount} fix items)`);

    result.fix_items_applied = fixItems.map(i =>
      `${i.quantity}x ${i.external_variant_id}`
    ).join(' | ');

    // 7. Dry-run: log what would change and stop
    if (!isLive) {
      result.status = 'DRY_RUN';
      if (needsCreate) {
        result.message += `Would CREATE bundle_selection for purchase_item ${purchaseItemId} with ${fixItems.length} items`;
        log(`  DRY RUN: Would POST /bundle_selections for purchase_item ${purchaseItemId} with ${fixItems.length} items`);
      } else {
        result.message += `Would update bundle_selection ${selection.id} with ${fixItems.length} items`;
        log(`  DRY RUN: Would PUT /bundle_selections/${selection.id} with ${fixItems.length} items`);
      }
      return result;
    }

    // 8. LIVE: Send PUT to Recharge
    // Build lookup of external_variant_id -> {collection_id, external_product_id} from current items
    const itemMetaMap = new Map();
    for (const ci of currentItems) {
      itemMetaMap.set(String(ci.external_variant_id), {
        collection_id: ci.collection_id,
        collection_source: ci.collection_source || 'shopify',
        external_product_id: ci.external_product_id,
      });
    }

    // Build the PUT payload with required fields
    const putItems = fixItems.map(i => {
      const meta = itemMetaMap.get(i.external_variant_id);
      if (!meta) {
        log(`  WARNING: No metadata for variant ${i.external_variant_id} - looking up from Recharge...`);
      }
      return {
        collection_id: meta ? meta.collection_id : '',
        collection_source: meta ? meta.collection_source : 'shopify',
        external_product_id: meta ? meta.external_product_id : '',
        external_variant_id: i.external_variant_id,
        quantity: i.quantity,
      };
    });

    // If any items missing metadata, try fetching from a fallback bundle selection
    const missingMeta = putItems.filter(p => !p.collection_id || !p.external_product_id);
    if (missingMeta.length > 0) {
      log(`  ${missingMeta.length} items missing metadata, fetching fallback selection...`);
      try {
        // Get all selections for this subscription to find metadata from other selections
        const allSelResp = await rcGet(`/bundle_selections?purchase_item_id=${subscription_id}&limit=250`);
        const allSels = allSelResp.bundle_selections || [];
        await sleep(DELAY_MS);
        for (const sel of allSels) {
          for (const item of (sel.items || [])) {
            const vid = String(item.external_variant_id);
            if (!itemMetaMap.has(vid)) {
              itemMetaMap.set(vid, {
                collection_id: item.collection_id,
                collection_source: item.collection_source || 'shopify',
                external_product_id: item.external_product_id,
              });
            }
          }
        }
        // Re-fill missing items
        for (const p of putItems) {
          if (!p.collection_id || !p.external_product_id) {
            const meta = itemMetaMap.get(p.external_variant_id);
            if (meta) {
              p.collection_id = meta.collection_id;
              p.collection_source = meta.collection_source;
              p.external_product_id = meta.external_product_id;
            }
          }
        }
      } catch (e) {
        log(`  WARNING: Failed to fetch fallback metadata: ${e.message}`);
      }
    }

    // If still missing, try fetching metadata from OTHER subscriptions' bundle_selections
    // that already have this variant (many subs share the same fallback items)
    const stillMissingAfterRC1 = putItems.filter(p => !p.collection_id || !p.external_product_id);
    if (stillMissingAfterRC1.length > 0) {
      log(`  ${stillMissingAfterRC1.length} items still missing after own selections, trying other subs...`);
      const missingVids = stillMissingAfterRC1.map(p => p.external_variant_id);
      // Find another subscription in allRows with the same variant that we can query
      const sameVariantRow = allRows.find(r =>
        r.subscription_id !== subscription_id &&
        r.fix_item_ids.includes(missingVids[0])
      );
      if (sameVariantRow) {
        try {
          const otherSelResp = await rcGet(`/bundle_selections?purchase_item_id=${sameVariantRow.subscription_id}&limit=250`);
          const otherSels = otherSelResp.bundle_selections || [];
          await sleep(DELAY_MS);
          for (const sel of otherSels) {
            for (const item of (sel.items || [])) {
              const vid = String(item.external_variant_id);
              if (!itemMetaMap.has(vid)) {
                itemMetaMap.set(vid, {
                  collection_id: item.collection_id,
                  collection_source: item.collection_source || 'shopify',
                  external_product_id: item.external_product_id,
                });
              }
            }
          }
          for (const p of putItems) {
            if (!p.collection_id || !p.external_product_id) {
              const meta = itemMetaMap.get(p.external_variant_id);
              if (meta) {
                p.collection_id = meta.collection_id;
                p.collection_source = meta.collection_source;
                p.external_product_id = meta.external_product_id;
                log(`  Got metadata for ${p.external_variant_id} from sub ${sameVariantRow.subscription_id}`);
              }
            }
          }
        } catch (e) {
          log(`  WARNING: Failed to fetch other sub selections: ${e.message}`);
        }
      }
    }

    // If still missing, resolve via Shopify GraphQL (variant -> product + collections)
    const stillMissingAfterRC = putItems.filter(p => !p.collection_id || !p.external_product_id);
    if (stillMissingAfterRC.length > 0) {
      log(`  ${stillMissingAfterRC.length} items still missing, resolving via Shopify...`);
      // Collect all known collection_ids from this bundle to match against
      const knownCollectionIds = new Set(putItems.filter(p => p.collection_id).map(p => String(p.collection_id)));

      for (const p of stillMissingAfterRC) {
        try {
          const gqlResp = await fetch(
            `https://${process.env.SHOPIFY_STORE_DOMAIN}/admin/api/${process.env.SHOPIFY_API_VERSION || '2024-07'}/graphql.json`,
            {
              method: 'POST',
              headers: {
                'X-Shopify-Access-Token': process.env.SHOPIFY_ADMIN_API_TOKEN,
                'Content-Type': 'application/json',
              },
              body: JSON.stringify({
                query: `{ productVariant(id: "gid://shopify/ProductVariant/${p.external_variant_id}") { product { id collections(first: 50) { edges { node { id } } } } } }`,
              }),
            },
          );
          const gqlData = await gqlResp.json();
          const variant = gqlData?.data?.productVariant;
          const productGid = variant?.product?.id || '';
          const productId = productGid.replace('gid://shopify/Product/', '');

          if (productId) {
            p.external_product_id = productId;

            // Find the collection that matches one of the bundle's known collections
            const productCollections = (variant?.product?.collections?.edges || [])
              .map(e => e.node.id.replace('gid://shopify/Collection/', ''));
            const matchingCollection = productCollections.find(c => knownCollectionIds.has(c));

            if (matchingCollection) {
              p.collection_id = matchingCollection;
              log(`  Resolved variant ${p.external_variant_id} -> product ${productId}, collection ${matchingCollection}`);
            } else {
              // Product not in any known bundle collection directly.
              // Check if product is in any of the bundle's expected collections via Shopify API
              log(`  Product ${productId} collections: ${productCollections.join(', ')} - none match known: ${[...knownCollectionIds].join(', ')}`);
              // Use Shopify to check which bundle collections contain this product
              let foundBundleCollection = '';
              for (const bcId of knownCollectionIds) {
                try {
                  const colResp = await fetch(
                    `https://${process.env.SHOPIFY_STORE_DOMAIN}/admin/api/${process.env.SHOPIFY_API_VERSION || '2024-07'}/graphql.json`,
                    {
                      method: 'POST',
                      headers: {
                        'X-Shopify-Access-Token': process.env.SHOPIFY_ADMIN_API_TOKEN,
                        'Content-Type': 'application/json',
                      },
                      body: JSON.stringify({
                        query: `{ collection(id: "gid://shopify/Collection/${bcId}") { hasProduct(id: "gid://shopify/Product/${productId}") } }`,
                      }),
                    },
                  );
                  const colData = await colResp.json();
                  if (colData?.data?.collection?.hasProduct) {
                    foundBundleCollection = bcId;
                    log(`  Found product ${productId} in bundle collection ${bcId}`);
                    break;
                  }
                  await sleep(100);
                } catch (e) {
                  log(`  WARNING: Collection check failed for ${bcId}: ${e.message}`);
                }
              }
              p.collection_id = foundBundleCollection || [...knownCollectionIds][0] || '';
              if (!foundBundleCollection) {
                log(`  WARNING: Product ${productId} not in any bundle collection, using ${p.collection_id} as fallback`);
              }
            }
            p.collection_source = 'shopify';

            itemMetaMap.set(p.external_variant_id, {
              collection_id: p.collection_id,
              collection_source: 'shopify',
              external_product_id: productId,
            });
          }
          await sleep(200);
        } catch (e) {
          log(`  WARNING: Shopify lookup failed for ${p.external_variant_id}: ${e.message}`);
        }
      }
    }

    // Final check - abort if still missing required fields
    const stillMissing = putItems.filter(p => !p.collection_id || !p.external_product_id);
    if (stillMissing.length > 0) {
      result.status = 'ERROR';
      result.message = `${stillMissing.length} items missing collection_id/external_product_id: ${stillMissing.map(p => p.external_variant_id).join(', ')}`;
      logError(`Sub ${subscription_id}: Missing metadata for ${stillMissing.length} items`);
      return result;
    }

    let updatedSelectionId;

    if (needsCreate) {
      // CREATE new bundle selection for the correct purchase_item_id
      log(`  CREATING bundle_selection for purchase_item ${purchaseItemId}...`);
      try {
        const createResp = await rcRequest('POST', '/bundle_selections', {
          purchase_item_id: purchaseItemId,
          items: putItems,
        });
        updatedSelectionId = createResp.bundle_selection?.id;
        log(`  Created bundle_selection ${updatedSelectionId}`);
        result.bundle_selection_id = updatedSelectionId;
        await sleep(DELAY_MS);
      } catch (createError) {
        result.status = 'ERROR';
        result.message = `POST failed: ${createError.message}`;
        logError(`Sub ${subscription_id}: POST /bundle_selections failed: ${createError.message}`);
        return result;
      }
    } else {
      // UPDATE existing bundle selection
      updatedSelectionId = selection.id;
      log(`  UPDATING bundle_selection ${selection.id}...`);
      try {
        await rcPut(`/bundle_selections/${selection.id}`, {
          items: putItems,
        });
        await sleep(DELAY_MS);
      } catch (putError) {
        result.status = 'ERROR';
        result.message = `PUT failed: ${putError.message}`;
        logError(`Sub ${subscription_id}: PUT /bundle_selections/${selection.id} failed: ${putError.message}`);
        return result;
      }
    }

    // 9. Post-update verification
    log(`  Verifying update...`);
    try {
      const verifyResp = await rcGet(`/bundle_selections/${updatedSelectionId}`);
      const updated = verifyResp.bundle_selection;
      await sleep(DELAY_MS);

      if (!updated || !updated.items) {
        result.status = 'VERIFY_FAILED';
        result.message = 'Could not re-fetch bundle selection after update';
        logError(`Sub ${subscription_id}: Post-verify failed - could not re-fetch`);
        return result;
      }

      const afterItems = updated.items || [];
      result.current_items_after = afterItems.map(i =>
        `${i.quantity}x ${i.external_variant_id}`
      ).join(' | ');

      const afterComparison = compareItems(afterItems, fixItems);
      result.match_after = afterComparison.matchPct;

      if (afterComparison.alreadyCorrect) {
        result.status = 'SUCCESS';
        result.message = `${needsCreate ? 'Created' : 'Updated'} and verified (${afterItems.length} items, 100% match)`;
        log(`  SUCCESS: Verified 100% match after ${needsCreate ? 'create' : 'update'}`);
      } else {
        result.status = 'VERIFY_MISMATCH';
        result.message = `${needsCreate ? 'Created' : 'Updated'} but post-verify shows ${afterComparison.matchPct}% match`;
        logError(`Sub ${subscription_id}: Post-verify mismatch: ${afterComparison.matchPct}%`);
      }
    } catch (verifyError) {
      result.status = 'VERIFY_FAILED';
      result.message = `${needsCreate ? 'Created' : 'Updated'} but verification failed: ${verifyError.message}`;
      logError(`Sub ${subscription_id}: Post-verify error: ${verifyError.message}`);
    }

    return result;
  } catch (e) {
    result.status = 'ERROR';
    result.message = e.message;
    logError(`Sub ${subscription_id}: ${e.message}`);
    return result;
  }
}

// ============================================================
// Checkpoint
// ============================================================
function loadCheckpoint() {
  if (fs.existsSync(CHECKPOINT_FILE)) {
    return JSON.parse(fs.readFileSync(CHECKPOINT_FILE, 'utf8'));
  }
  return { processedIds: [], lastIndex: 0 };
}

function saveCheckpoint(checkpoint) {
  fs.writeFileSync(CHECKPOINT_FILE, JSON.stringify(checkpoint, null, 2));
}

// ============================================================
// Main
// ============================================================
async function main() {
  const args = process.argv.slice(2);

  let mode = 'dry-run';
  let testSubId = null;
  let limit = 0; // 0 = no limit

  if (args.includes('--test') && args[args.indexOf('--test') + 1]) {
    mode = 'test';
    testSubId = args[args.indexOf('--test') + 1];
  } else if (args.includes('--test-live') && args[args.indexOf('--test-live') + 1]) {
    mode = 'test-live';
    testSubId = args[args.indexOf('--test-live') + 1];
  } else if (args.includes('--live')) {
    mode = 'live';
  }

  if (args.includes('--limit') && args[args.indexOf('--limit') + 1]) {
    limit = parseInt(args[args.indexOf('--limit') + 1], 10);
  }

  log(`========================================`);
  log(`fix-bundle-selections.js`);
  log(`Mode: ${mode.toUpperCase()}`);
  if (testSubId) log(`Test subscription: ${testSubId}`);
  if (limit) log(`Limit: ${limit} rows`);
  log(`RC tokens: ${RC_TOKENS.length}`);
  log(`Input: ${INPUT_CSV}`);
  log(`========================================`);

  if (mode === 'live') {
    log('');
    log('*** LIVE MODE - This will modify Recharge data ***');
    log('*** Press Ctrl+C within 10 seconds to abort ***');
    log('');
    await sleep(10000);
    log('Proceeding with live updates...');
  }

  // Initialize error log
  fs.writeFileSync(ERROR_LOG, `# Error log - Mode: ${mode} - Started: ${new Date().toISOString()}\n\n`);

  // Load input CSV
  log('Loading input CSV...');
  const csvRaw = fs.readFileSync(INPUT_CSV, 'utf8');
  const csvRows = parseCSV(csvRaw);
  const headers = csvRows[0];

  const colIdx = {};
  headers.forEach((h, i) => colIdx[h] = i);

  const allRows = csvRows.slice(1).map(r => ({
    subscription_id: r[colIdx['subscription_id']],
    customer_id: r[colIdx['customer_id']],
    email: r[colIdx['email']],
    sku: r[colIdx['sku']],
    fix_type: r[colIdx['fix_type']],
    fix_item_ids: r[colIdx['fix_item_ids'] ?? colIdx['expected_item_ids']],
    current_item_ids: r[colIdx['current_item_ids']],
    current_item_count: r[colIdx['current_item_count']],
    fix_item_count: r[colIdx['fix_item_count'] ?? colIdx['expected_item_count']],
  }));

  log(`Loaded ${allRows.length} rows from CSV`);

  // Filter for test mode
  let rowsToProcess;
  if (testSubId) {
    rowsToProcess = allRows.filter(r => r.subscription_id === testSubId);
    if (rowsToProcess.length === 0) {
      log(`Subscription ${testSubId} not found in CSV. Aborting.`);
      process.exit(1);
    }
  } else {
    rowsToProcess = allRows;
  }

  if (limit && !testSubId) {
    rowsToProcess = rowsToProcess.slice(0, limit);
    log(`Limited to first ${rowsToProcess.length} rows`);
  }

  // Load checkpoint (batch mode only)
  let checkpoint = { processedIds: new Set(), lastIndex: 0 };
  if (!testSubId) {
    const raw = loadCheckpoint();
    checkpoint.processedIds = new Set(raw.processedIds || []);
    if (checkpoint.processedIds.size > 0) {
      log(`Resuming from checkpoint: ${checkpoint.processedIds.size} already processed`);
    }
  }

  // Initialize output files
  const resultHeaders = [
    'subscription_id', 'customer_id', 'email', 'sku', 'fix_type',
    'bundle_selection_id', 'status', 'message',
    'current_items_before', 'fix_items_applied', 'current_items_after',
    'match_before', 'match_after',
  ];
  if (!fs.existsSync(RESULTS_CSV) || testSubId) {
    fs.writeFileSync(RESULTS_CSV, resultHeaders.join(',') + '\n');
  }
  if (!fs.existsSync(BACKUP_CSV) || testSubId) {
    fs.writeFileSync(BACKUP_CSV, 'subscription_id,bundle_selection_id,current_items\n');
  }

  // Process
  const startTime = Date.now();
  const stats = {
    total: rowsToProcess.length,
    processed: 0, success: 0, dry_run: 0, already_correct: 0,
    skipped: 0, error: 0, verify_mismatch: 0, verify_failed: 0,
  };

  for (let i = 0; i < rowsToProcess.length; i++) {
    const row = rowsToProcess[i];

    if (checkpoint.processedIds.has(row.subscription_id)) continue;

    // Progress every 50 rows or on test
    if (i % 50 === 0 || testSubId) {
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(0);
      const rate = stats.processed > 0 ? (stats.processed / (elapsed / 60)).toFixed(1) : '?';
      log(`\nProgress: ${i + 1}/${rowsToProcess.length} (${elapsed}s, ${rate}/min)`);
      log(`  Stats: ${stats.success} ok, ${stats.dry_run} dry, ${stats.already_correct} correct, ${stats.skipped} skip, ${stats.error} err`);
    }

    log(`\n[${i + 1}/${rowsToProcess.length}] Sub ${row.subscription_id} (${row.fix_type}, ${row.sku})`);

    const result = await processSubscription(row, mode, allRows);

    // Stats
    stats.processed++;
    switch (result.status) {
      case 'SUCCESS': stats.success++; break;
      case 'DRY_RUN': stats.dry_run++; break;
      case 'ALREADY_CORRECT': stats.already_correct++; break;
      case 'SKIPPED': stats.skipped++; break;
      case 'VERIFY_MISMATCH': stats.verify_mismatch++; break;
      case 'VERIFY_FAILED': stats.verify_failed++; break;
      default: stats.error++; break;
    }

    // Append to results CSV
    const resultRow = resultHeaders.map(h => escapeCSV(result[h] != null ? result[h] : '')).join(',');
    fs.appendFileSync(RESULTS_CSV, resultRow + '\n');

    // Append to backup CSV
    if (result.current_items_before) {
      fs.appendFileSync(BACKUP_CSV,
        `${row.subscription_id},${result.bundle_selection_id},${escapeCSV(result.current_items_before)}\n`
      );
    }

    // Checkpoint every 10 rows
    if (!testSubId) {
      checkpoint.processedIds.add(row.subscription_id);
      if (i % 10 === 0) {
        saveCheckpoint({ processedIds: [...checkpoint.processedIds], lastIndex: i });
      }
    }

    await sleep(DELAY_MS);
  }

  // Final checkpoint
  if (!testSubId) {
    saveCheckpoint({ processedIds: [...checkpoint.processedIds], lastIndex: rowsToProcess.length });
  }

  // Summary
  const totalMin = ((Date.now() - startTime) / 1000 / 60).toFixed(1);
  log(`\n========================================`);
  log(`COMPLETE (${totalMin} min)`);
  log(`========================================`);
  log(`Mode: ${mode.toUpperCase()}`);
  log(`Total: ${stats.total} | Processed: ${stats.processed}`);
  log(`  SUCCESS: ${stats.success}`);
  log(`  DRY_RUN: ${stats.dry_run}`);
  log(`  ALREADY_CORRECT: ${stats.already_correct}`);
  log(`  SKIPPED: ${stats.skipped}`);
  log(`  ERROR: ${stats.error}`);
  log(`  VERIFY_MISMATCH: ${stats.verify_mismatch}`);
  log(`  VERIFY_FAILED: ${stats.verify_failed}`);
  log(`\nResults: ${RESULTS_CSV}`);
  log(`Backup: ${BACKUP_CSV}`);
  log(`Errors: ${ERROR_LOG}`);
}

main().catch(e => {
  logError(`Fatal: ${e.message}\n${e.stack}`);
  process.exit(1);
});
