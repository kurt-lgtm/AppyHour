/* Matrix Commander — Frontend Logic */

const $ = id => document.getElementById(id);
const status = msg => { $('statusText').innerHTML = msg; };

// ── File Upload (drag & drop + click) ──────────────────────────────

function setupDropZone(zoneId, fileType) {
  const zone = $(zoneId);
  const fileNameEl = $(fileType === 'main' ? 'mainFileName' : 'giftFileName');
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.xlsx';

  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0], fileType);
  });
  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    if (input.files.length) uploadFile(input.files[0], fileType);
  });

  async function uploadFile(file, type) {
    status(`<span class="spinner"></span>Uploading ${file.name}...`);
    const form = new FormData();
    form.append('file', file);
    form.append('type', type);

    try {
      const resp = await fetch('/api/upload', { method: 'POST', body: form });
      const data = await resp.json();
      if (data.ok) {
        fileNameEl.textContent = data.filename;
        zone.classList.add('loaded');
        if (type === 'main') $('btnValidate').disabled = false;
        status(`Loaded: ${data.filename}`);
      } else {
        status(`Error: ${data.error}`);
      }
    } catch (e) {
      status(`Upload failed: ${e.message}`);
    }
  }
}

// ── Validation ─────────────────────────────────────────────────────

async function runValidation() {
  $('btnValidate').disabled = true;
  status('<span class="spinner"></span>Validating...');

  const body = {
    ship_day: $('shipDay').value,
    ship_date: $('shipDate').value,
  };

  try {
    const resp = await fetch('/api/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();

    if (data.error) {
      status(`Error: ${data.error}`);
      $('btnValidate').disabled = false;
      return;
    }

    showValidation(data);

    // Show inventory panel
    $('inventory-panel').classList.remove('hidden');

    status(data.all_passed ? 'All checks passed' : 'Validation issues found');
    $('btnValidate').disabled = false;
  } catch (e) {
    status(`Validation failed: ${e.message}`);
    $('btnValidate').disabled = false;
  }
}

// ── Inventory ──────────────────────────────────────────────────────

async function loadInventory(file) {
  status('<span class="spinner"></span>Loading inventory...');

  const form = new FormData();
  if (file) form.append('file', file);

  try {
    const resp = await fetch('/api/inventory', { method: 'POST', body: form });
    const data = await resp.json();

    if (data.error) {
      status(`Error: ${data.error}`);
      return;
    }

    $('skuCount').textContent = data.sku_count;
    $('shortageCount').textContent = data.shortage_count;
    $('shortageCount').style.color = data.shortage_count > 0 ? 'var(--red)' : 'var(--green)';

    // Render inventory table
    const tbody = data.table.map(r => {
      const cls = r.status === 'SHORT' ? 'short' : (r.status === 'LOW' ? 'low' : 'ok');
      const badge = r.status === 'SHORT' ? '<span style="color:var(--red)">SHORT</span>' :
                    r.status === 'LOW' ? '<span style="color:var(--yellow)">LOW</span>' :
                    '<span style="color:var(--green)">OK</span>';
      return `<tr class="${cls}">
        <td>${r.sku}</td><td>${escHtml(r.name)}</td>
        <td class="num">${r.demand}</td><td class="num">${r.available}</td>
        <td class="num">${r.net}</td><td class="status-badge">${badge}</td></tr>`;
    }).join('');

    $('inventoryTable').innerHTML = `<table>
      <thead><tr><th>SKU</th><th>Name</th><th>Demand</th><th>Avail</th><th>Net</th><th>Status</th></tr></thead>
      <tbody>${tbody}</tbody></table>`;

    // Render swap panel if shortages
    if (data.shortages.length > 0) {
      renderSwaps(data.shortages);
    }

    // Show actions
    $('actions-panel').classList.remove('hidden');

    status(`Inventory loaded: ${data.sku_count} SKUs, ${data.shortage_count} shortages`);
  } catch (e) {
    status(`Inventory check failed: ${e.message}`);
  }
}

// ── Swaps ──────────────────────────────────────────────────────────

function renderSwaps(shortages) {
  const panel = $('swap-panel');
  panel.classList.remove('hidden');

  const list = $('swapList');
  list.innerHTML = shortages.map((s, idx) => {
    let candidates = '';
    if (s.candidates.length > 0) {
      candidates = '<div class="swap-candidates">' + s.candidates.map((c, ci) => {
        const rec = ci === 0 ? ' recommended' : '';
        return `<button class="swap-candidate${rec}" onclick="applySwap('${s.sku}','${c.sku}',${Math.min(c.surplus, s.shortage)})">${c.sku} <span class="surplus">(+${c.surplus})</span></button>`;
      }).join('') + '</div>';
    } else {
      candidates = '<div class="no-candidates">No swap candidates -- manual resolution needed</div>';
    }

    return `<div class="swap-card">
      <div class="swap-header">
        <span class="swap-sku">${s.sku}</span>
        <span class="swap-shortage">SHORT ${s.shortage}</span>
      </div>
      <div class="swap-name">${escHtml(s.name)} | Demand: ${s.demand} | Available: ${s.available}</div>
      ${candidates}
    </div>`;
  }).join('');
}

async function applySwap(shortSku, replacementSku, qty) {
  status(`<span class="spinner"></span>Applying swap: ${shortSku} -> ${replacementSku} (${qty})...`);

  try {
    const resp = await fetch('/api/swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ short_sku: shortSku, replacement_sku: replacementSku, qty }),
    });
    const data = await resp.json();

    if (data.error) {
      status(`Swap failed: ${data.error}`);
      return;
    }

    status(`Swap applied. ${data.remaining_shortages} shortages remaining.`);
    // Reload inventory to refresh the table
    await loadInventory(null);
  } catch (e) {
    status(`Swap failed: ${e.message}`);
  }
}

// ── Finalize ───────────────────────────────────────────────────────

async function runFinalize() {
  $('btnFinalize').disabled = true;
  status('<span class="spinner"></span>Finalizing...');

  const body = {
    ship_day: $('shipDay').value,
    ship_date: $('shipDate').value,
  };

  try {
    const resp = await fetch('/api/finalize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();

    if (data.error) {
      let msg = `Error: ${data.error}`;
      if (data.details) msg += '<br>' + data.details.map(d => escHtml(d)).join('<br>');
      $('finalResult').innerHTML = `<span style="color:var(--red)">${msg}</span>`;
      status('Finalize failed');
    } else {
      $('finalResult').innerHTML = `Ready to email: <strong>${escHtml(data.filename)}</strong>`;
      status(`Finalized: ${data.filename}`);
    }
  } catch (e) {
    $('finalResult').innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
    status('Finalize failed');
  }
  $('btnFinalize').disabled = false;
}

// ── Generate from Shopify ──────────────────────────────────────────

async function runGenerate() {
  const tag = $('rmfgTag').value.trim();
  if (!tag) {
    status('Enter an RMFG tag (e.g. RMFG_20260328)');
    return;
  }

  $('btnGenerate').disabled = true;
  status('<span class="spinner"></span>Generating matrix from Shopify... (this may take 30-60s)');

  const body = {
    tag,
    ship_day: $('shipDay').value,
    ship_date: $('shipDate').value,
  };

  try {
    const resp = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();

    if (data.error) {
      status(`Error: ${data.error}`);
      $('btnGenerate').disabled = false;
      return;
    }

    // Show the generated filename in the main drop zone
    $('mainFileName').textContent = data.filename;
    document.getElementById('mainDrop').classList.add('loaded');

    // Show validation (auto-run by generate)
    showValidation(data);

    // Show inventory panel
    $('inventory-panel').classList.remove('hidden');

    status(`Generated: ${data.filename} (${data.order_count} orders)`);
  } catch (e) {
    status(`Generate failed: ${e.message}`);
  }
  $('btnGenerate').disabled = false;
}

// ── Shared validation display ─────────────────────────────────────

function showValidation(data) {
  const panel = $('validation-panel');
  panel.classList.remove('hidden');

  $('orderCount').textContent = data.order_count;
  $('regularCount').textContent = data.regular_count;
  $('giftCount').textContent = data.gift_count;

  const list = $('checksList');
  list.innerHTML = data.checks.map(c => {
    const icon = c.passed ? '<span class="check-icon pass">PASS</span>' : '<span class="check-icon fail">FAIL</span>';
    let details = '';
    if (!c.passed && c.details.length) {
      details = '<div class="check-details">' + c.details.map(d => `<div>${escHtml(d)}</div>`).join('') + '</div>';
    }
    return `<div class="check-item">${icon}<span class="check-name">${escHtml(c.name)}</span><span class="check-msg">${escHtml(c.message)}</span></div>${details}`;
  }).join('');
}

// ── Helpers ────────────────────────────────────────────────────────

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── Init ───────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  setupDropZone('mainDrop', 'main');
  setupDropZone('giftDrop', 'gift');

  $('btnGenerate').addEventListener('click', runGenerate);
  $('btnValidate').addEventListener('click', runValidation);
  $('btnLoadInventory').addEventListener('click', () => loadInventory(null));
  $('inventoryFile').addEventListener('change', e => {
    if (e.target.files.length) loadInventory(e.target.files[0]);
  });
  $('btnFinalize').addEventListener('click', runFinalize);

  // Set default ship date to next Monday
  const today = new Date();
  const dayOfWeek = today.getDay();
  const daysUntilMonday = (8 - dayOfWeek) % 7 || 7;
  const nextMon = new Date(today);
  nextMon.setDate(today.getDate() + daysUntilMonday);
  $('shipDate').value = nextMon.toISOString().split('T')[0];

  status('Ready -- enter RMFG tag and click Generate, or drop an XLSX');
});
