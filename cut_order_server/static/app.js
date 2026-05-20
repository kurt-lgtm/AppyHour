// Cut Order — UI logic + localStorage defaults
(() => {
  const LS_PREFIX = "cutorder.";
  let cachedRatios = null;

  // ─── Locks + defaults (localStorage) ─────────────────────────────────
  function applyDefaults() {
    document.querySelectorAll("input[type='number'], input[type='text']").forEach(el => {
      const sku = el.dataset.sku;
      const key = sku ? `${LS_PREFIX}override.${sku}` : (el.id ? `${LS_PREFIX}${el.id}` : null);
      if (!key) return;
      const locked = localStorage.getItem(`${key}.locked`) === "1";
      const val = localStorage.getItem(key);
      if (locked && val != null) el.value = val;
      // Mark lock button
      const lockBtn = document.querySelector(`.lock[data-key="${key.replace(LS_PREFIX,'')}"]`);
      if (lockBtn && locked) lockBtn.classList.add("locked");
    });
  }

  document.addEventListener("click", e => {
    const btn = e.target.closest(".lock");
    if (!btn) return;
    const targetKey = btn.dataset.key;
    if (!targetKey) return;
    const lsKey = `${LS_PREFIX}${targetKey.startsWith("ahb-") || targetKey.startsWith("bl-") ? `override.${targetKey.split("-").slice(1).join("-")}` : targetKey}`;
    const sku = targetKey.startsWith("ahb-") || targetKey.startsWith("bl-") ? targetKey.split("-").slice(1).join("-") : null;
    const input = sku
      ? document.querySelector(`input[data-sku="${sku}"]`)
      : document.getElementById(targetKey);
    if (!input) return;
    const nowLocked = !btn.classList.contains("locked");
    if (nowLocked) {
      localStorage.setItem(lsKey, input.value);
      localStorage.setItem(`${lsKey}.locked`, "1");
      btn.classList.add("locked");
    } else {
      localStorage.removeItem(lsKey);
      localStorage.removeItem(`${lsKey}.locked`);
      btn.classList.remove("locked");
    }
  });

  // ─── Add custom SKU row ──────────────────────────────────────────────
  document.getElementById("addCustomBtn")?.addEventListener("click", () => {
    const input = document.getElementById("customSku");
    const sku = (input.value || "").trim().toUpperCase();
    if (!sku) return;
    const grid = input.closest(".grid");
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `
      <label>${sku}</label>
      <input type="number" min="0" step="1" name="override" data-sku="${sku}" placeholder="0">
      <button type="button" class="remove" title="Remove">×</button>
    `;
    grid.insertBefore(row, input.closest(".add-custom"));
    input.value = "";
  });

  document.addEventListener("click", e => {
    if (e.target.classList.contains("remove")) {
      e.target.closest(".row").remove();
    }
  });

  // ─── Fetch empirical ratios ──────────────────────────────────────────
  document.getElementById("fetchRatios").addEventListener("click", async () => {
    const status = document.getElementById("ratiosStatus");
    status.textContent = "Computing… (~30s)";
    try {
      const r = await fetch("/multiplier/ratios", { method: "POST", headers: { "Content-Type": "application/json" } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      cachedRatios = await r.json();
      const skuCount = Object.keys(cachedRatios).filter(k => k !== "__global__").length;
      const global = cachedRatios.__global__ ? cachedRatios.__global__.toFixed(2) : "?";
      status.textContent = `✅ Global ratio: ${global} · ${skuCount} per-SKU ratios`;
    } catch (err) {
      status.textContent = `❌ ${err.message}`;
    }
  });

  // ─── Submit run ──────────────────────────────────────────────────────
  document.getElementById("runForm").addEventListener("submit", async e => {
    e.preventDefault();
    const status = document.getElementById("runStatus");
    const btn = document.getElementById("runBtn");
    status.textContent = "Running… (~60s)";
    btn.disabled = true;

    const overrides = {};
    document.querySelectorAll("input[name='override']").forEach(el => {
      const sku = el.dataset.sku;
      const v = parseInt(el.value, 10);
      if (sku && Number.isFinite(v) && v > 0) overrides[sku] = v;
    });

    const knob = parseFloat(document.getElementById("knob").value || "1.0");

    const payload = {
      overrides,
      multiplier_knob: knob,
      empirical_ratios: cachedRatios || {},
    };

    try {
      const r = await fetch("/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status}: ${txt.slice(0, 200)}`);
      }
      const data = await r.json();
      const link = document.getElementById("downloadLink");
      link.href = data.download_url;
      link.textContent = `Download ${data.filename}`;
      document.getElementById("resultMeta").textContent = JSON.stringify({
        wk1_end: data.wk1_end,
        snapshot_date: data.snapshot_date,
        ship_tags: data.ship_tags,
        demand_total_skus: data.demand_total_skus,
        cut_rows: data.cut_rows,
        run_id: data.run_id,
      }, null, 2);
      document.getElementById("result").hidden = false;
      status.textContent = "✅ Done";
    } catch (err) {
      status.textContent = `❌ ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });

  applyDefaults();
})();
