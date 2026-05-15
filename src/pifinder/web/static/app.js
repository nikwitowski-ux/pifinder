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
  // No clustering. Every firm gets its own pin. When pins are close together
  // at the current zoom, clicking ANY pin in the neighborhood opens a single
  // popup that lists every firm within `PROXIMITY_PX` pixels — the
  // apartments.com pattern: visible individual pins, multi-card on interaction.
  const PROXIMITY_PX = 26;
  const HOVER_DELAY_MS = 140;

  function initMap() {
    const map = L.map("map", {
      zoomControl: true,
      scrollWheelZoom: true,
    }).setView([33.7, -117.85], 10);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
    }).addTo(map);

    state.map = map;
    state.pinLayer = L.layerGroup().addTo(map);
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
    marker.firm = firm;

    marker.on("click", () => {
      const group = neighborhoodFor(firm);
      openMultiPopup(marker, group, { sticky: true });
      // Sync the grid selection to the *clicked* pin (not the whole group).
      selectFirm(firm.id, { from: "map" });
    });
    // Quick hover preview — only if the user lingers; doesn't fight a click.
    marker.on("mouseover", () => {
      clearTimeout(marker._hoverTimer);
      marker._hoverTimer = setTimeout(() => {
        const group = neighborhoodFor(firm);
        openMultiPopup(marker, group, { sticky: false });
      }, HOVER_DELAY_MS);
    });
    marker.on("mouseout", () => {
      clearTimeout(marker._hoverTimer);
    });
    return marker;
  }

  // Find every firm whose marker is within PROXIMITY_PX of the anchor's marker
  // at the current zoom level. The anchor is always first in the result.
  function neighborhoodFor(anchorFirm) {
    const anchorMarker = state.markersByFirmId.get(anchorFirm.id);
    if (!anchorMarker || !state.map) return [anchorFirm];
    const anchorPt = state.map.latLngToContainerPoint(anchorMarker.getLatLng());

    const result = [anchorFirm];
    for (const f of state.firms) {
      if (f.id === anchorFirm.id) continue;
      const m = state.markersByFirmId.get(f.id);
      if (!m) continue;
      const p = state.map.latLngToContainerPoint(m.getLatLng());
      const dx = p.x - anchorPt.x;
      const dy = p.y - anchorPt.y;
      if (Math.sqrt(dx * dx + dy * dy) <= PROXIMITY_PX) result.push(f);
    }
    // After the anchor, sort by score desc so the strongest leads surface first.
    const head = result[0];
    const tail = result.slice(1).sort((a, b) => (b.score || 0) - (a.score || 0));
    return [head, ...tail];
  }

  function openMultiPopup(anchorMarker, firms, { sticky }) {
    const html = firms.length === 1 ? singleCardHtml(firms[0]) : multiCardHtml(firms);
    const popupOpts = {
      maxWidth: 320,
      minWidth: 240,
      closeButton: sticky,
      autoClose: sticky,
      closeOnClick: sticky,
      className: firms.length > 1 ? "multi-popup-wrap" : "single-popup-wrap",
    };
    anchorMarker.unbindPopup();
    anchorMarker.bindPopup(html, popupOpts).openPopup();
  }

  function singleCardHtml(firm) {
    const phoneRow = firm.phone ? `<span class="pop-meta">${escapeHtml(firm.phone)}</span><br>` : "";
    const siteRow  = firm.website
      ? `<a class="pop-cta" href="${escapeAttr(firm.website)}" target="_blank" rel="noopener">visit site →</a> `
      : "";
    const score = firm.score != null ? firm.score : "—";
    const bucket = (firm.bucket || bucketFor(firm.score)).toUpperCase();
    return `
      <span class="pop-name">${escapeHtml(firm.name)}</span>
      <span class="pop-meta">${escapeHtml(firm.city || "")}${firm.state ? ", " + escapeHtml(firm.state) : ""}</span><br>
      ${phoneRow}
      <span class="pop-meta">score ${score} · ${bucket}</span><br>
      ${siteRow}
      <a class="pop-cta" href="/firm/${firm.id}">open →</a>
    `;
  }

  // apartments.com-style multi-view: title + scrollable list of compact cards.
  function multiCardHtml(firms) {
    const rows = firms.map((f) => {
      const b = (f.bucket || bucketFor(f.score));
      const score = f.score != null ? f.score : "—";
      const phone = f.phone ? `<span class="multi-phone">${escapeHtml(f.phone)}</span>` : "";
      const meta = [
        `<span class="multi-score ${b}">${score}</span>`,
        f.city ? `<span class="multi-city">${escapeHtml(f.city)}${f.state ? ", " + escapeHtml(f.state) : ""}</span>` : "",
        phone,
      ].filter(Boolean).join("");
      return `
        <li class="multi-row">
          <a class="multi-name" href="/firm/${f.id}">${escapeHtml(f.name)}</a>
          <div class="multi-meta">${meta}</div>
        </li>
      `;
    }).join("");
    return `
      <div class="multi-popup">
        <div class="multi-header">
          <span class="multi-count">${firms.length}</span>
          <span class="multi-headline">firms at this location</span>
        </div>
        <ol class="multi-list">${rows}</ol>
      </div>
    `;
  }

  // ---- grid ----
  function initGrid() {
    state.table = new Tabulator("#grid", {
      data: [],
      // Salesforce-style: every column always visible, horizontal scroll inside
      // the pane. Score + Firm are frozen so they stay visible while you scroll
      // right through the rest of the fields.
      layout: "fitData",
      placeholder: "no firms — run `pifinder discover` first.",
      selectableRows: 1,
      headerSortClickElement: "icon",
      tooltips: true,
      columns: [
        { title: "Score", field: "score", width: 86, frozen: true,
          sorter: numericSorter, formatter: scoreCell, hozAlign: "left",
          resizable: false },
        { title: "Firm", field: "name", width: 240, minWidth: 220, frozen: true,
          formatter: nameCell, cssClass: "cell-name" },
        { title: "Location", field: "_loc", width: 170, minWidth: 140,
          mutator: (_, row) => [row.city, row.state].filter(Boolean).join(", ") || "—" },
        { title: "Phone", field: "phone", width: 150, cssClass: "mono",
          formatter: (c) => c.getValue() || "—" },
        { title: "Reviews", field: "user_ratings_total", width: 90,
          sorter: numericSorter, cssClass: "mono cell-rating",
          formatter: (c) => c.getValue() == null ? "—" : c.getValue() },
        { title: "Rating", field: "rating", width: 80,
          sorter: numericSorter, cssClass: "mono cell-rating",
          formatter: (c) => c.getValue() == null ? "—" : Number(c.getValue()).toFixed(1) },
        { title: "Att.", field: "attorney_count", width: 70,
          sorter: numericSorter, cssClass: "mono",
          formatter: (c) => c.getValue() == null ? "—" : c.getValue() },
        { title: "Size", field: "_size", width: 110,
          formatter: sizeCell, sorter: sizeSorter,
          headerTooltip: "Solo / Small (2-5) / Mid (6-15) / Large (16+), from attorney_count" },
        { title: "Est. Rev", field: "_rev", width: 110, cssClass: "mono cell-rev-col",
          formatter: revenueCell, sorter: revenueSorter,
          headerTooltip: "Heuristic estimate: $500K–$1.5M per attorney, scaled by review volume. Not measured." },
        { title: "PI", field: "has_pi_practice_page", width: 60,
          formatter: yesNoCell },
        { title: "Website", field: "website", width: 240, cssClass: "mono",
          formatter: websiteCell },
        { title: "Address", field: "address", width: 280,
          formatter: (c) => c.getValue() || "—" },
        { title: "Zip", field: "postal_code", width: 80, cssClass: "mono",
          formatter: (c) => c.getValue() || "—" },
        { title: "Est.", field: "established_year", width: 70, cssClass: "mono",
          sorter: numericSorter,
          formatter: (c) => c.getValue() == null ? "—" : c.getValue() },
        { title: "Last post", field: "last_website_post_at", width: 110,
          cssClass: "mono",
          formatter: (c) => {
            const v = c.getValue();
            return v ? String(v).slice(0, 10) : "—";
          } },
        { title: "Place ID", field: "place_id", width: 200, cssClass: "mono cell-dim",
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

  function websiteCell(cell) {
    const v = cell.getValue();
    if (!v) return "—";
    const display = String(v).replace(/^https?:\/\//, "").replace(/\/$/, "");
    return `<a href="${escapeAttr(v)}" target="_blank" rel="noopener" class="cell-website" title="${escapeAttr(v)}">${escapeHtml(display)} ↗</a>`;
  }

  // ---- size + revenue heuristics ----
  // Size: directly derived from attorney_count. Stable, no fudge.
  function sizeBucket(att) {
    if (att == null || att === 0) return null;
    if (att === 1) return "Solo";
    if (att <= 5) return "Small";
    if (att <= 15) return "Mid";
    return "Large";
  }
  function sizeOrder(att) {
    const order = { Solo: 1, Small: 2, Mid: 3, Large: 4 };
    return order[sizeBucket(att)] || 0;
  }
  function sizeCell(cell) {
    const att = cell.getRow().getData().attorney_count;
    const b = sizeBucket(att);
    if (!b) return "—";
    return `<span class="cell-size size-${b.toLowerCase()}">${b}</span><span class="cell-size-att">${att}</span>`;
  }

  // Revenue estimate: industry rule-of-thumb for plaintiff PI is $500K–$1.5M
  // revenue per attorney per year. Review volume scales the per-attorney
  // multiplier (more reviews ~> more market presence). This is NOT measured —
  // the column header carries "Est." to flag that. Replace with a real data
  // source (ZoomInfo etc.) when accuracy matters.
  function revenueEstimateMillions(att, reviews) {
    if (att == null || att === 0) return null;
    const r = Math.max(reviews || 1, 1);
    // Multiplier: 0.6 at no reviews, climbing to ~1.5 at very high volume.
    const mult = Math.min(1.5, 0.6 + Math.log10(r) * 0.3);
    const point = att * 750_000 * mult;          // midpoint of $500K..$1.5M
    return point / 1_000_000;
  }
  function revenueCell(cell) {
    const f = cell.getRow().getData();
    const m = revenueEstimateMillions(f.attorney_count, f.user_ratings_total);
    if (m == null) return "—";
    let display;
    if (m < 1)       display = "<$1M";
    else if (m < 10) display = `~$${m.toFixed(1)}M`;
    else             display = `~$${Math.round(m)}M`;
    return `<span class="cell-rev">${display}</span>`;
  }
  function revenueSorter(_a, _b, aRow, bRow) {
    const av = revenueEstimateMillions(aRow.getData().attorney_count, aRow.getData().user_ratings_total) ?? -1;
    const bv = revenueEstimateMillions(bRow.getData().attorney_count, bRow.getData().user_ratings_total) ?? -1;
    return av - bv;
  }
  function sizeSorter(_a, _b, aRow, bRow) {
    return sizeOrder(aRow.getData().attorney_count) - sizeOrder(bRow.getData().attorney_count);
  }

  // ---- coordination ----
  function selectFirm(id, opts = {}) {
    state.selectedId = id;
    const marker = state.markersByFirmId.get(id);
    const firm = state.firms.find((f) => f.id === id);

    if (marker && firm && opts.from !== "map") {
      const ll = marker.getLatLng();
      state.map.flyTo(ll, Math.max(state.map.getZoom(), 13), { duration: 0.5 });
      // When the fly settles, open the multi-popup so neighbors are surfaced too.
      state.map.once("moveend", () => {
        const group = neighborhoodFor(firm);
        openMultiPopup(marker, group, { sticky: true });
      });
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

    // Redraw map: individual pins, no clustering. Multi-firm popups appear
    // on click/hover when neighbors fall within PROXIMITY_PX.
    state.pinLayer.clearLayers();
    state.markersByFirmId.clear();
    const pts = [];
    for (const f of state.firms) {
      const m = markerForFirm(f);
      if (!m) continue;
      state.pinLayer.addLayer(m);
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
