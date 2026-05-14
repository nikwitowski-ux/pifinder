// pifinder dashboard JS — bidirectional sync between Leaflet map and Tabulator grid.
// Vanilla; no build step.

(() => {
  "use strict";

  const buckets = window.PIFINDER_BUCKETS || { red_max: 39, yellow_max: 69 };
  const colors = {
    hot:      getCSS("--hot"),
    warm:     getCSS("--warm"),
    cold:     getCSS("--cold"),
    unscored: getCSS("--unscored"),
    ink:      getCSS("--ink"),
    bg:       getCSS("--bg"),
  };

  // ---- state ----
  const state = {
    firms: [],
    markersByFirmId: new Map(),
    table: null,
    map: null,
    cluster: null,
    selectedId: null,
  };

  // ---- map ----
  function initMap() {
    const map = L.map("map", {
      zoomControl: true,
      scrollWheelZoom: true,
    }).setView([33.7, -117.85], 10);  // Orange County default

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
    }).addTo(map);

    const cluster = L.markerClusterGroup({
      showCoverageOnHover: false,
      maxClusterRadius: 45,
      iconCreateFunction: (c) => L.divIcon({
        html: `<div>${c.getChildCount()}</div>`,
        className: "marker-cluster",
        iconSize: L.point(32, 32),
      }),
    });
    map.addLayer(cluster);

    state.map = map;
    state.cluster = cluster;
  }

  function bucketFor(score) {
    if (score === null || score === undefined) return "unscored";
    if (score <= buckets.red_max) return "cold";
    if (score <= buckets.yellow_max) return "warm";
    return "hot";
  }

  function markerForFirm(firm) {
    if (firm.latitude == null || firm.longitude == null) return null;
    const bucket = firm.bucket || bucketFor(firm.score);
    const radius = Math.max(5, Math.min(14, 4 + Math.sqrt(firm.user_ratings_total || 0) / 2));
    const marker = L.circleMarker([firm.latitude, firm.longitude], {
      radius,
      fillColor: colors[bucket] || colors.unscored,
      color: colors.ink,
      weight: 1,
      fillOpacity: 0.92,
    });
    marker.firmId = firm.id;
    marker.bindPopup(popupHtml(firm), { maxWidth: 260, closeButton: false });
    marker.on("click", () => selectFirm(firm.id, { from: "map" }));
    return marker;
  }

  function popupHtml(firm) {
    const phoneRow = firm.phone ? `<span class="pop-meta">${escapeHtml(firm.phone)}</span><br>` : "";
    const siteRow  = firm.website
      ? `<a class="pop-cta" href="${escapeAttr(firm.website)}" target="_blank" rel="noopener">visit site →</a> `
      : "";
    const score = firm.score != null ? firm.score : "—";
    return `
      <span class="pop-name">${escapeHtml(firm.name)}</span>
      <span class="pop-meta">${escapeHtml(firm.city || "")}${firm.state ? ", " + escapeHtml(firm.state) : ""}</span><br>
      ${phoneRow}
      <span class="pop-meta">score ${score} · ${(firm.bucket || bucketFor(firm.score)).toUpperCase()}</span><br>
      ${siteRow}
      <a class="pop-cta" href="/firm/${firm.id}">open →</a>
    `;
  }

  // ---- grid ----
  function initGrid() {
    state.table = new Tabulator("#grid", {
      data: [],
      layout: "fitColumns",
      placeholder: "no firms — run `pifinder discover` first.",
      selectableRows: 1,
      headerSortClickElement: "icon",
      columns: [
        { title: "Score", field: "score", width: 90, sorter: numericSorter,
          formatter: scoreCell, hozAlign: "left" },
        { title: "Firm", field: "name", widthGrow: 3, formatter: nameCell, cssClass: "cell-name" },
        { title: "City", field: "city", widthGrow: 2 },
        { title: "St", field: "state", width: 60, cssClass: "mono" },
        { title: "Reviews", field: "user_ratings_total", width: 90, sorter: numericSorter,
          cssClass: "mono cell-rating",
          formatter: (c) => c.getValue() == null ? "—" : c.getValue() },
        { title: "Rating", field: "rating", width: 80, sorter: numericSorter,
          cssClass: "mono cell-rating",
          formatter: (c) => c.getValue() == null ? "—" : Number(c.getValue()).toFixed(1) },
        { title: "Att.", field: "attorney_count", width: 70, sorter: numericSorter,
          cssClass: "mono",
          formatter: (c) => c.getValue() == null ? "—" : c.getValue() },
        { title: "PI", field: "has_pi_practice_page", width: 60,
          formatter: yesNoCell },
        { title: "Phone", field: "phone", widthGrow: 2, cssClass: "mono",
          formatter: (c) => c.getValue() || "—" },
      ],
    });

    state.table.on("rowClick", (e, row) => {
      const firm = row.getData();
      selectFirm(firm.id, { from: "grid" });
    });
  }

  function numericSorter(a, b) {
    const av = a == null ? -Infinity : a;
    const bv = b == null ? -Infinity : b;
    return av - bv;
  }

  function scoreCell(cell) {
    const v = cell.getValue();
    const firm = cell.getRow().getData();
    const bucket = firm.bucket || bucketFor(v);
    return `<span class="cell-score ${bucket}"><span class="pip"></span>${v == null ? "—" : v}</span>`;
  }

  function nameCell(cell) {
    const firm = cell.getRow().getData();
    return `<a href="/firm/${firm.id}">${escapeHtml(firm.name)}</a>`;
  }

  function yesNoCell(cell) {
    const v = cell.getValue();
    if (v === true)  return `<span class="cell-yesno yes">yes</span>`;
    if (v === false) return `<span class="cell-yesno">no</span>`;
    return `<span class="cell-yesno">—</span>`;
  }

  // ---- coordination ----
  function selectFirm(id, opts = {}) {
    state.selectedId = id;
    const marker = state.markersByFirmId.get(id);

    if (marker && opts.from !== "map") {
      const ll = marker.getLatLng();
      state.map.flyTo(ll, Math.max(state.map.getZoom(), 13), { duration: 0.5 });
      // open the popup once the marker is visible (cluster may have to spiderfy)
      state.cluster.zoomToShowLayer(marker, () => marker.openPopup());
    }
    if (opts.from !== "grid" && state.table) {
      const row = state.table.getRow(id);
      if (row) {
        state.table.deselectRow();
        row.select();
        row.scrollTo("center");
      }
    }
  }

  // ---- fetching / filtering ----
  async function reload() {
    const params = new URLSearchParams();
    const q = document.getElementById("f-q").value.trim();
    const minScore = Number(document.getElementById("f-score").value);
    const hasWebsite = document.getElementById("f-website").checked;
    const enrichedOnly = document.getElementById("f-enriched").checked;
    if (q) params.set("q", q);
    if (minScore > 0) params.set("min_score", String(minScore));
    if (hasWebsite) params.set("has_website", "true");
    if (enrichedOnly) params.set("enriched_only", "true");

    const r = await fetch(`/api/firms?${params}`);
    const data = await r.json();
    state.firms = data.firms;
    document.getElementById("grid-count").textContent = `${data.count} shown`;

    // Redraw grid
    state.table.setData(state.firms);

    // Redraw map
    state.cluster.clearLayers();
    state.markersByFirmId.clear();
    const pts = [];
    for (const f of state.firms) {
      const m = markerForFirm(f);
      if (!m) continue;
      state.cluster.addLayer(m);
      state.markersByFirmId.set(f.id, m);
      pts.push([f.latitude, f.longitude]);
    }
    if (pts.length > 0) state.map.fitBounds(pts, { padding: [30, 30], maxZoom: 12 });
  }

  // ---- wiring ----
  function wireFilters() {
    const q = document.getElementById("f-q");
    const sc = document.getElementById("f-score");
    const scOut = document.getElementById("f-score-out");
    const ws = document.getElementById("f-website");
    const en = document.getElementById("f-enriched");

    q.addEventListener("input", debounce(reload, 200));
    sc.addEventListener("input", () => { scOut.textContent = sc.value; });
    sc.addEventListener("change", reload);
    ws.addEventListener("change", reload);
    en.addEventListener("change", reload);

    document.getElementById("btn-export").addEventListener("click", () => {
      const params = currentParamsString();
      window.location.href = `/api/export.csv?${params}`;
    });
    document.getElementById("btn-reset").addEventListener("click", () => {
      q.value = ""; sc.value = 0; scOut.textContent = 0;
      ws.checked = true; en.checked = false;
      reload();
    });
  }

  function currentParamsString() {
    const params = new URLSearchParams();
    const q = document.getElementById("f-q").value.trim();
    const minScore = Number(document.getElementById("f-score").value);
    const hasWebsite = document.getElementById("f-website").checked;
    const enrichedOnly = document.getElementById("f-enriched").checked;
    if (q) params.set("q", q);
    if (minScore > 0) params.set("min_score", String(minScore));
    if (hasWebsite) params.set("has_website", "true");
    if (enrichedOnly) params.set("enriched_only", "true");
    return params.toString();
  }

  // ---- helpers ----
  function debounce(fn, ms) {
    let t = null;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }
  function getCSS(v) {
    return getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
  function escapeAttr(s) { return escapeHtml(s).replace(/"/g, "&quot;"); }

  // ---- boot ----
  if (document.body.dataset.page === "index") {
    initMap();
    initGrid();
    wireFilters();
    reload();
  }
})();
