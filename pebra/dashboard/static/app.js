// PEBRA Risk Observatory — instrument deck (Phase 5d).
//
// One classic script (no ES-module import graph) so the strict CSP stays exactly `script-src 'nonce-'`.
// Vanilla DOM; uPlot (vendored global) draws the calibration + time-series charts; Cytoscape.js
// (vendored global, WebGL renderer) draws the codebase graph. No inline style attributes anywhere —
// the CSP forbids them; dynamic sizing uses the CSSOM `.style.prop` setter, not governed by style-src.
(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const token = params.get("token") || "";
  const repo = params.get("repo") || "";
  const LIVE = params.get("live") === "1";
  const LIVE_MS = 1500;

  const RAMP = {  // decision -> risk-ramp colour (mirrors style.css)
    proceed: "#3fb950", revise_safer: "#d6a419", ask_human: "#f0883e",
    inspect_first: "#f0883e", block: "#f85149", reject: "#f85149",
  };
  const BENEFIT = "#58a6ff";
  const RISK = "#f0883e";
  const UTILITY = "#59d3bd";
  const ACCENT = "#59d3bd";
  const GRID = "#29333d";
  const AXIS = "#8795a1";

  const app = document.getElementById("app");
  const DEV_MODE = app && app.dataset.devMode === "1";
  const boot = document.getElementById("boot");
  const liveDot = document.getElementById("live-dot");
  const repoChip = document.getElementById("repo-chip");
  const chainPill = document.getElementById("chain-pill");
  let graphSeq = 0;
  let riskSeq = 0;
  const tabEverLoaded = new Set();  // tabs whose data has loaded once — skeletons show on first load only

  // Human labels for the audit-chain counts (never the raw table names).
  const chainLabels = {};
  chainLabels["assessments"] = "Assessments run";
  chainLabels["outcomes"] = "Completed outcomes";
  chainLabels["prediction_" + "errors"] = "Predictions checked";
  chainLabels["risk_snapshots"] = "Learning snapshots";
  chainLabels["learned_" + "risk_" + "facts"] = "Learned rules";

  async function getJSON(path) {
    // Send the bearer only when we actually have one — the loopback default runs token-free, and an
    // empty `Authorization: Bearer` header is pointless (and would 401 a token-required server).
    const headers = token ? { Authorization: "Bearer " + token } : {};
    const res = await fetch(path, { headers });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function fmt(x, d) { return x == null || Number.isNaN(x) ? "—" : Number(x).toFixed(d == null ? 3 : d); }
  function fmtPct(x, d) { return x == null || Number.isNaN(x) ? "—" : (Number(x) * 100).toFixed(d == null ? 0 : d) + "%"; }
  function fmtLossPoints(x) { return x == null || Number.isNaN(x) ? "—" : Number(x).toFixed(2) + " loss pts"; }
  function tailPath(path) {
    const parts = String(path || "").split(/[\\/]/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "—";
  }
  function formatTask(task) { return task ? String(task) : "—"; }
  function formatTarget(paths) {
    if (!Array.isArray(paths) || !paths.length) return "—";
    return paths.length === 1 ? tailPath(paths[0]) : tailPath(paths[0]) + " +" + (paths.length - 1);
  }
  function formatFingerprint(value) { return value ? String(value).slice(0, 12) : "—"; }
  function lessonIndicator(entry, status) {
    if (status === "unavailable") return "unavailable";
    return entry ? "learned" : "—";
  }
  function pct(x) { return x == null ? "—" : (100 * x).toFixed(0) + "%"; }
  function pill(decision) {
    const p = el("span", "pill " + (decision || ""), decision || "—");
    return p;
  }
  function card(title) {
    const c = el("section", "card");
    if (title) c.appendChild(el("h2", "card-title", title));
    return c;
  }
  function stat(label, value, foot) {
    const c = card();
    c.appendChild(el("p", "eyebrow", label));
    c.appendChild(el("div", "stat-value", value));
    if (foot) c.appendChild(el("div", "stat-foot", foot));
    return c;
  }
  function emptyMsg(text) { return el("p", "empty", text); }

  // ---- audit-chain pill (always refreshed, incl. live) ----
  async function refreshChain() {
    try {
      const chain = await getJSON("/api/chain-status");
      chainPill.textContent = "audit chain: " + (chain.valid ? "valid" : "BROKEN");
      chainPill.className = "chain-pill " + (chain.valid ? "valid" : "broken");
      return chain;
    } catch (e) {
      chainPill.textContent = "audit chain: unreachable";
      return null;
    }
  }

  // ---- Activity (merged Overview + History) ----
  // One secondary tab for assessment telemetry so Graph can be the default product surface.
  const historyState = { assessment_id: null };

  async function renderActivity(view) {
    const [overview, series, chain, data, lessons] = await Promise.all([
      getJSON(rp("/overview")),
      getJSON(rp("/scores-series?limit=500")),
      refreshChain(),
      getJSON(rp("/assessments?limit=100")),
      getJSON(rp("/learning/context?limit=200")).catch(() => ({ status: "unavailable", items: [] })),
    ]);
    clear(view);
    const confs = series.items.map((i) => i.scores.edit_confidence).filter((x) => x != null);
    const meanConf = confs.length ? confs.reduce((a, b) => a + b, 0) / confs.length : null;
    const proceed = overview.by_decision.proceed || 0;
    const counts = (chain && chain.counts) || {};

    const row = el("div", "grid stat-row");
    row.appendChild(stat("Assessments run", String(overview.total)));
    row.appendChild(stat("Proceed rate", overview.total ? pct(proceed / overview.total) : "—",
      proceed + " of " + overview.total));
    row.appendChild(stat("Mean edit-confidence", meanConf == null ? "—" : fmt(meanConf, 2)));
    row.appendChild(stat("Learned rules", String(counts["learned_" + "risk_" + "facts"] || 0),
      chain && chain.valid ? "chain valid" : "chain BROKEN"));
    view.appendChild(row);

    const dcard = card("Decisions");
    dcard.appendChild(decisionBar(overview.by_decision, overview.total));
    view.appendChild(dcard);

    // Audit chain stays available but collapsed so KPI + history stay primary.
    const chainDetails = document.createElement("details");
    chainDetails.className = "card hotspots-details";
    const chainSummary = document.createElement("summary");
    chainSummary.textContent = "Audit chain · " + (chain && chain.valid ? "valid" : "check status");
    chainDetails.appendChild(chainSummary);
    const chainBody = el("div", "hotspots-body");
    const list = el("div", "dist-legend");
    Object.keys(chainLabels).forEach((k) => {
      list.appendChild(el("span", null, (chainLabels[k]) + ": " + (counts[k] || 0)));
    });
    chainBody.appendChild(list);
    chainDetails.appendChild(chainBody);
    view.appendChild(chainDetails);

    const lessonByAssessment = {};
    if (lessons.status !== "unavailable") {
      (lessons.items || []).forEach((item) => { lessonByAssessment[item.assessment_id] = item; });
    }

    const tcard = card("Risk, benefit & expected utility over time");
    const chartBox = el("div", "chart");
    tcard.appendChild(chartBox);
    view.appendChild(tcard);
    drawSeries(chartBox, series.items);

    // Assessment drill-in shows the persisted prior source and post-verify RCA benefit.
    const bcard = card("Assessment detail");
    bcard.dataset.testid = "assessment-detail";
    const bbody = el("div");
    bbody.appendChild(el("p", "chart-note", "Select a row to see its prior source and measured RCA benefit."));
    bcard.appendChild(bbody);

    const hcard = card("Recent assessments");
    if (!data.items.length) { hcard.appendChild(emptyMsg("No assessments recorded yet. Run pebra assess to add the first one.")); }
    else {
      const table = el("table");
      table.appendChild(headRow([
        "assessment", "task", "target", "fingerprint", "decision",
        { label: "expected loss", cls: "num" },
        { label: "benefit", cls: "num" },
        { label: "expected utility", cls: "num" },
        { label: "rau", cls: "num" },
        { label: "confidence", cls: "num" },
        "outcome",
        "lesson",
      ]));
      const tb = el("tbody");
      data.items.forEach((it) => {
        const s = it.scores || {};
        const tr = el("tr", "clickable");
        tr.appendChild(cell(it.assessment_id, "mono"));
        tr.appendChild(cell(formatTask(it.task)));
        tr.appendChild(cell(formatTarget(it.target_files), "mono"));
        tr.appendChild(cell(formatFingerprint(it.candidate_fingerprint), "mono"));
        const dcell = el("td"); dcell.appendChild(pill(it.decision)); tr.appendChild(dcell);
        tr.appendChild(cell(fmtLossPoints(s.expected_loss), "num"));
        tr.appendChild(cell(fmtPct(s.benefit), "num"));
        tr.appendChild(cell(fmt(s.expected_utility), "num"));
        tr.appendChild(cell(fmt(s.rau), "num"));
        tr.appendChild(cell(fmt(s.edit_confidence, 2), "num"));
        tr.appendChild(cell(it.terminal_status || "pending", "mono"));
        tr.appendChild(cell(lessonIndicator(lessonByAssessment[it.assessment_id], lessons.status), "mono"));
        tr.addEventListener("click", function () {
          historyState.assessment_id = it.assessment_id;
          showMeasuredBenefit(it.assessment_id, bbody, it, lessonIndicator(lessonByAssessment[it.assessment_id], lessons.status));
        });
        tb.appendChild(tr);
      });
      table.appendChild(tb);
      const tableScroll = el("div", "table-scroll");
      tableScroll.appendChild(table);
      hcard.appendChild(tableScroll);
    }
    view.appendChild(hcard);
    view.appendChild(bcard);
    if (historyState.assessment_id) {
      const restore = data.items.find(function (x) { return x.assessment_id === historyState.assessment_id; });
      if (restore) {
        showMeasuredBenefit(restore.assessment_id, bbody, restore, lessonIndicator(lessonByAssessment[restore.assessment_id], lessons.status));
      }
    }
  }

  function decisionBar(byDecision, total) {
    const wrap = el("div");
    const bar = el("div", "distbar");
    const legend = el("div", "dist-legend");
    const summary = [];
    Object.keys(byDecision).forEach((d) => {
      const n = byDecision[d];
      const pct = total ? Math.round(100 * n / total) : 0;
      const seg = el("span");
      seg.style.width = (total ? (100 * n / total) : 0) + "%";
      seg.style.background = RAMP[d] || "#5c6773";
      // Segments are pure colour decoration; the bar carries the full text summary for a screen reader.
      seg.setAttribute("aria-hidden", "true");
      bar.appendChild(seg);
      const item = el("span", null, d + " " + n);
      const sw = el("span", "swatch");
      sw.style.background = RAMP[d] || "#5c6773";
      item.prepend(sw);
      legend.appendChild(item);
      summary.push(d + " " + pct + "%");
    });
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", "Decision mix: " + (summary.join(", ") || "no data"));
    wrap.appendChild(bar);
    wrap.appendChild(legend);
    return wrap;
  }

  // Fetch one assessment's detail and render its measured (verify-time) RCA benefit. The measured signal
  // lives on a post_assessment_guardrails row (measured_benefit + measured_benefit_deltas), exposed by
  // GET /api/repos/{repo}/assessments/{id}. Distinct from the assess-time projected `benefit` in the
  // table.
  function rowIdentityTable(row, lessonText) {
    // The narrow-viewport table hides fingerprint/rau/confidence/lesson; surface them here so a row
    // click on a small screen still exposes every column's value (assessment id shows in the table below).
    const s = row.scores || {};
    const t = el("table");
    t.appendChild(headRow(["row detail", "value"]));
    const tb = el("tbody");
    function textRow(k, v) {
      const tr = el("tr");
      tr.appendChild(cell(k, "mono"));
      tr.appendChild(cell(v, "mono"));
      tb.appendChild(tr);
    }
    textRow("task", formatTask(row.task));
    textRow("target", formatTarget(row.target_files));
    textRow("fingerprint", formatFingerprint(row.candidate_fingerprint));
    const dr = el("tr");
    dr.appendChild(cell("decision", "mono"));
    const dc = el("td"); dc.appendChild(pill(row.decision)); dr.appendChild(dc);
    tb.appendChild(dr);
    textRow("rau", fmt(s.rau));
    textRow("edit confidence", fmt(s.edit_confidence, 2));
    textRow("lesson", lessonText || "—");
    t.appendChild(tb);
    return t;
  }

  async function showMeasuredBenefit(id, box, row, lessonText) {
    clear(box);
    if (row) box.appendChild(rowIdentityTable(row, lessonText));
    const detail = el("div");
    detail.appendChild(el("p", "chart-note", "loading " + id + "…"));
    box.appendChild(detail);
    try {
      const d = await getJSON(rp("/assessments/" + encodeURIComponent(id)));
      const rows = (d.guardrails || []);
      const g = rows.filter(function (x) {
        return x && x.measured_benefit_deltas && Object.keys(x.measured_benefit_deltas).length;
      }).pop();
      clear(detail);
      const prior = d.prior_provenance || { source: "cold_start", calibration_tags: [] };
      const priorTable = el("table");
      priorTable.appendChild(headRow(["prior measure", "value"]));
      const priorBody = el("tbody");
      [["Prior source", prior.source || "cold_start"],
       ["Calibration version", (prior.calibration_tags || []).join(", ") || "none"]].forEach(function (kv) {
        const tr = el("tr");
        tr.appendChild(cell(kv[0], "mono"));
        tr.appendChild(cell(kv[1], "mono"));
        priorBody.appendChild(tr);
      });
      priorTable.appendChild(priorBody);
      detail.appendChild(priorTable);
      if (!g || g.measured_benefit == null) {
        detail.appendChild(emptyMsg("No verify / measured-benefit recorded for " + id + " yet. Run pebra verify to record it."));
        return;
      }
      const dl = g.measured_benefit_deltas || {};
      const t = el("table");
      t.appendChild(headRow(["measure", "value"]));
      const tb = el("tbody");
      [["assessment", id], ["measured_benefit", fmt(g.measured_benefit)],
       ["complexity_delta", fmt(dl.complexity_delta)],
       ["maintainability_index_delta", fmt(dl.maintainability_index_delta)]].forEach(function (kv) {
        const tr = el("tr");
        tr.appendChild(cell(kv[0], "mono"));
        tr.appendChild(cell(kv[1], "num"));
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      detail.appendChild(t);
    } catch (e) {
      clear(detail);
      detail.appendChild(emptyMsg("Error loading " + id + ": " + e.message));
    }
  }

  // ---- Calibration ----
  const calState = { target_type: "risk_binary", scope: "production" };
  async function renderCalibration(view) {
    clear(view);
    const c = card("Calibration");
    const controls = el("div", "controls");
    controls.appendChild(sel("target", calState.target_type,
      [["risk_binary", "risk (binary)"], ["benefit_binary", "benefit (binary)"],
       ["benefit_continuous", "benefit (continuous)"],
       ["cost_continuous", "review cost (continuous)"]],
      (v) => { calState.target_type = v; renderCalibration(view); }));
    controls.appendChild(sel("scope", calState.scope,
      [["production", "production"], ["all", "all observed"]],
      (v) => { calState.scope = v; renderCalibration(view); }));
    c.appendChild(controls);
    const chartBox = el("div", "chart");
    c.appendChild(chartBox);
    const note = el("p", "chart-note", "loading…");
    c.appendChild(note);
    view.appendChild(c);

    const data = await getJSON(
      rp("/calibration?target_type=" + calState.target_type + "&scope=" + calState.scope));
    note.textContent = data.sample_count + " labelled sample(s) · perfect calibration = the diagonal";
    if (!data.sample_count) { clear(chartBox); chartBox.appendChild(emptyMsg("No labelled predictions in this scope yet. Calibration fills in as outcomes are recorded.")); return; }
    if (data.scatter && data.scatter.length) drawScatter(chartBox, data.scatter);
    else drawReliability(chartBox, data.bins);
  }

  // ---- Learning ----
  async function renderLearning(view) {
    const [snaps, facts, lessons] = await Promise.all([
      getJSON(rp("/learning/snapshots?limit=50")), getJSON(rp("/learning/facts?limit=200")),
      getJSON(rp("/learning/context?limit=200")),
    ]);
    clear(view);
    const scard = card("Learning snapshots");
    if (!snaps.items.length) scard.appendChild(emptyMsg("No snapshots yet — the learning loop hasn't run."));
    else {
      const t = el("table");
      t.appendChild(headRow(["snapshot", "status", "reason", "drift", "created"]));
      const tb = el("tbody");
      snaps.items.forEach((s) => {
        const tr = el("tr");
        tr.appendChild(cell(s.snapshot_id, "mono"));
        tr.appendChild(cell(s.status, "mono"));
        tr.appendChild(cell(s.promotion_reason || s.rollback_reason || "—", "mono"));
        tr.appendChild(cell(fmt(s.drift_score, 3), "num"));
        tr.appendChild(cell((s.created_at || "").slice(0, 19), "mono"));
        tb.appendChild(tr);
      });
      t.appendChild(tb); scard.appendChild(t);
    }
    view.appendChild(scard);

    const fcard = card("Learned rules");
    if (!facts.items.length) fcard.appendChild(emptyMsg("No learned rules yet. Rules appear once the learning loop promotes a snapshot."));
    else {
      const t = el("table");
      t.appendChild(headRow(["target", "type", "scope", "status"]));
      const tb = el("tbody");
      facts.items.forEach((f) => {
        const tr = el("tr");
        tr.appendChild(cell(f.target_name, "mono"));
        tr.appendChild(cell(f.target_type, "mono"));
        tr.appendChild(cell((f.scope_kind || "global") + (f.scope_value ? ":" + f.scope_value : ""), "mono"));
        tr.appendChild(cell(f.status, "mono"));
        tb.appendChild(tr);
      });
      t.appendChild(tb); fcard.appendChild(t);
    }
    view.appendChild(fcard);

    const lcard = card("Verified lessons");
    if (lessons.status === "unavailable") {
      lcard.appendChild(emptyMsg("Verified lesson history is unavailable or failed integrity validation."));
    } else if (!lessons.items.length) {
      lcard.appendChild(emptyMsg("No verified completed outcomes have produced recallable lessons yet. This fills in once a proceed decision reaches a verified completed outcome."));
    } else {
      const t = el("table");
      t.appendChild(headRow(["record", "assessment", "task", "lesson", "verified outcome", "created"]));
      const tb = el("tbody");
      lessons.items.forEach((item) => {
        const tr = el("tr");
        tr.appendChild(cell(item.learning_context_id, "mono"));
        tr.appendChild(cell(item.assessment_id, "mono"));
        tr.appendChild(cell(item.task));
        tr.appendChild(cell(item.lesson));
        tr.appendChild(cell(item.verification_summary || item.terminal_status));
        tr.appendChild(cell((item.created_at || "").slice(0, 19), "mono"));
        tb.appendChild(tr);
      });
      t.appendChild(tb); lcard.appendChild(t);
    }
    view.appendChild(lcard);
  }

  // ---- Codebase graph (Cytoscape.js, WebGL) ----
  // Structural palette: dark-legible and deliberately distinct from risk/benefit signal colours.
  // Structural colours stay neutral/violet so they cannot be mistaken for the green→red risk ramp or
  // benefit blue. Risk mode still replaces these fills with decision colours and shapes.
  const FILE_COLOR = "#8b949e";
  const SYMBOL_COLOR = "#a78bfa";
  const SYMBOL_LABEL_ZOOM = 1;
  const HUB_COLORS = ["#2dd4bf", "#58a6ff", "#a78bfa", "#f0883e", "#f778ba", "#d6a419"];
  const SPOKE_COLORS = ["#2dd4bf", "#58a6ff", "#a78bfa", "#f0883e", "#f778ba", "#d6a419"];
  // Risk overlay: colour + SHAPE per decision (categorical — paired shape keeps it colourblind-safe and
  // deliberately distinct from the structural file/symbol colours so "risk view" never reads as "kind view").
  const RISK_DECISIONS = ["proceed", "test_first", "inspect_first", "revise_safer", "ask_human", "reject"];
  const DECISION_STYLE = {
    proceed: { color: "#3fb950", shape: "ellipse" },
    test_first: { color: "#58a6ff", shape: "round-rectangle" },
    inspect_first: { color: "#d6a419", shape: "diamond" },
    revise_safer: { color: "#f0883e", shape: "triangle" },
    ask_human: { color: "#a371f7", shape: "hexagon" },
    reject: { color: "#f85149", shape: "vee" },
    other: { color: "#8b949e", shape: "pentagon" },
  };
  const graphState = {
    cy: null, mode: null, inspector: null,
    overlay: "structure", risk: null, assessmentId: null,
    riskLegend: null, riskCaption: null,
    learning: null, learningLegend: null,
    resizeObserver: null,
    symbolLabelsVisible: null,
  };

  function disableRiskOverlay() {
    graphState.overlay = "structure";
    graphState.risk = null;
    applyOverlay();  // strip stale rb-* classes from the current graph before hiding the controls
    if (graphState.riskLegend) graphState.riskLegend.hidden = true;
    if (graphState.riskCaption) graphState.riskCaption.hidden = true;
  }

  async function renderGraph(view) {
    destroyCy();  // tear down the old WebGL/Cytoscape instance before removing its container
    clear(view);

    // Hero first: codebase graph owns the viewport. Hotspots are secondary and collapsed.
    const graphCard = card("Codebase graph");
    graphCard.classList.add("hero");  // the map is the page's hero — step it up one surface tier
    const controls = el("div", "controls");
    graphCard.appendChild(controls);
    const cyEl = el("div", "graph-cy");
    cyEl.id = "graph-cy";
    graphCard.appendChild(cyEl);
    graphCard.appendChild(graphLegend());
    const riskLegend = el("div", "graph-legend risk-legend");
    riskLegend.hidden = true;  // enabled only after a fresh symbol graph has assessment risk loaded
    buildRiskLegend(riskLegend);
    graphCard.appendChild(riskLegend);
    graphState.riskLegend = riskLegend;
    const learningLegend = el("div", "graph-legend learning-legend");
    learningLegend.hidden = true;
    learningLegend.appendChild(swatchLabel("#f778ba", "verified lesson (learning_context)"));
    graphCard.appendChild(learningLegend);
    graphState.learningLegend = learningLegend;
    const searchResults = el("div", "search-results");
    graphCard.appendChild(searchResults);
    // Inspector: the accessibility fallback for the canvas graph (no per-node DOM). Keyboard-reachable
    // and populated on node selection — from a canvas tap OR from activating a focusable search result.
    const inspector = el("div", "inspector");
    inspector.id = "graph-inspector";
    inspector.setAttribute("tabindex", "0");
    inspector.setAttribute("role", "region");
    inspector.setAttribute("aria-label", "Selected graph node");
    inspector.appendChild(el("p", "chart-note", "Select a node (or a search result) to inspect it."));
    graphState.inspector = inspector;
    buildGraphControls(controls, searchResults, inspector, cyEl);
    graphCard.appendChild(inspector);
    view.appendChild(graphCard);

    // Collapsed fan-in ranking: one summary line until expanded; fetch only on first open.
    const hotspots = document.createElement("details");
    hotspots.className = "card hotspots-details";
    hotspots.setAttribute("data-testid", "repo-hotspots");
    const summary = document.createElement("summary");
    summary.textContent = "Repo hotspots (highest inbound fan-in)";
    hotspots.appendChild(summary);
    const hotBody = el("div", "hotspots-body");
    hotBody.appendChild(el("p", "chart-note", "Expand to load fan-in ranking."));
    hotspots.appendChild(hotBody);
    view.appendChild(hotspots);
    let hotspotsLoaded = false;
    hotspots.addEventListener("toggle", function () {
      if (!hotspots.open || hotspotsLoaded) return;
      hotspotsLoaded = true;
      loadHotspotsTable(hotBody, summary);
    });

    if (DEV_MODE) {
      const god = el("button", "graph-btn", "God map");
      const full = el("button", "graph-btn", "Full graph (debug)");
      [god, full].forEach(function (b) { b.setAttribute("type", "button"); });
      god.addEventListener("click", async function () {
        await loadGodNodeMap(cyEl, graphCard);
        await setupRiskOverlay(controls);
        await loadLearningOverlay();
      });
      full.addEventListener("click", async function () {
        await loadFullGraph(cyEl, graphCard);
        await setupRiskOverlay(controls);
        await loadLearningOverlay();
      });
      controls.appendChild(god);
      controls.appendChild(full);
    }
    // Graph load starts immediately — not gated on hotspots network I/O.
    await loadGodNodeMap(cyEl, graphCard);
    await setupRiskOverlay(controls);
    await loadLearningOverlay();
  }

  async function loadHotspotsTable(box, summary) {
    clear(box);
    box.appendChild(el("p", "chart-note", "loading…"));
    try {
      const ov = await getJSON(rp("/graph/overview?top_n=15"));
      clear(box);
      if (!ov.available) {
        box.appendChild(fallback(ov));
        return;
      }
      if (!ov.files.length) {
        box.appendChild(emptyMsg("No fan-in in the current graph."));
        return;
      }
      const top = ov.files[0];
      summary.textContent = "Repo hotspots · "
        + (ov.total_file_count != null ? ov.total_file_count + " files" : ov.files.length + " shown")
        + " · highest fan-in " + (top ? top.distinct_caller_count : "—");
      const t = el("table");
      t.appendChild(headRow(["file", "dependents"]));
      const tb = el("tbody");
      ov.files.forEach((f) => {
        const tr = el("tr");
        const fileCell = el("td");
        const focus = el("button", "hotspot-link", f.file_path);
        focus.setAttribute("type", "button");
        focus.addEventListener("click", function () {
          // Focus matching graph nodes when the live graph has that file path.
          focusGraphPath(f.file_path);
        });
        fileCell.appendChild(focus);
        tr.appendChild(fileCell);
        tr.appendChild(cell(String(f.distinct_caller_count), "num"));
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      box.appendChild(t);
      if (ov.truncated) {
        box.appendChild(el("p", "chart-note", "top " + ov.files.length + " of " + ov.total_file_count));
      }
    } catch (e) {
      clear(box);
      box.appendChild(fallback("graph overview unavailable"));
    }
  }

  function focusGraphPath(filePath) {
    const cy = graphState.cy;
    if (!cy || !filePath) return;
    const want = normPath(filePath);
    const hits = cy.nodes().filter(function (n) {
      return normPath(n.data("file_path")) === want;
    });
    if (!hits.nonempty()) return;
    cy.elements().addClass("search-dim");
    hits.removeClass("search-dim").addClass("search-hit");
    hits.connectedEdges().removeClass("search-dim");
    cy.animate({ fit: { eles: hits, padding: 80 }, duration: 280 });
    hits[0].select();
    if (graphState.inspector) showInspector(graphState.inspector, hits[0].data(), false);
  }

  async function loadLearningOverlay() {
    if (!graphState.cy) return;  // no rendered graph → nothing to badge
    let res;
    try {
      res = await getJSON(rp("/learning/context?limit=200"));
    } catch (e) {
      return;
    }
    // Only VERIFIED learning_context lessons drive badges; anything else (empty/unavailable) shows no
    // badge and no warning — never derived from raw completed outcome rows.
    if (!res || res.status !== "available" || !res.items || !res.items.length) {
      graphState.learning = null;
      applyLearningBadges();
      updateLearningLegend();
      return;
    }
    // Prototype-free maps: node file paths / symbols are untrusted keys, so a node literally named
    // "constructor"/"toString"/etc. must NOT match an inherited Object.prototype member and get a
    // fabricated lesson badge.
    const byFile = Object.create(null);
    const bySymbol = Object.create(null);
    res.items.forEach((it) => {
      (it.target_files || []).forEach((f) => {
        const k = normPath(f);
        if (k && !(k in byFile)) byFile[k] = it;
      });
      (it.symbols || []).forEach((sy) => {
        if (sy && !(sy in bySymbol)) bySymbol[sy] = it;
      });
    });
    graphState.learning = { byFile: byFile, bySymbol: bySymbol };
    applyLearningBadges();
    updateLearningLegend();
  }

  function nodeLesson(d) {
    // Match only on TRUSTED identity keys: file_path and qualified_name. `label` is a lossy display
    // tail ("A::B::c" -> "c") — matching it would badge every node graph-wide that shares a common
    // short name (run/get/__init__), misrepresenting which nodes a verified lesson actually touches.
    const L = graphState.learning;
    if (!L) return null;
    const fp = normPath(d.file_path);
    if (fp && L.byFile[fp]) return L.byFile[fp];
    if (d.qualified_name && L.bySymbol[d.qualified_name]) return L.bySymbol[d.qualified_name];
    return null;
  }

  function applyLearningBadges() {
    const cy = graphState.cy;
    if (!cy) return;
    cy.nodes().removeClass("has-lesson");
    if (!graphState.learning) return;
    cy.nodes().forEach((n) => { if (nodeLesson(n.data())) n.addClass("has-lesson"); });
  }

  function updateLearningLegend() {
    if (graphState.learningLegend) graphState.learningLegend.hidden = !graphState.learning;
  }

  function appendLearningDetail(inspector, d) {
    const lesson = nodeLesson(d);
    if (!lesson) return;
    inspector.appendChild(el("div", "insp-sep"));
    inspector.appendChild(inspRow("learning", "verified lesson"));  // verified, NOT "promoted"
    inspector.appendChild(inspRow("lesson", lesson.lesson || "—"));
    inspector.appendChild(el("p", "insp-note", "Source: verified learning_context"));
  }

  async function setupRiskOverlay(controls) {
    controls.querySelectorAll(".overlay-toggle, .risk-caption, .chart-note").forEach((n) => n.remove());
    graphState.riskCaption = null;
    if (!graphState.cy) {
      disableRiskOverlay();
      return;  // no rendered graph (unavailable / stale) → no overlay controls
    }
    if (!riskEligibleMode(graphState.mode)) {
      disableRiskOverlay();
      const note = el("p", "chart-note");
      note.textContent = "Risk overlay unavailable in collapsed mode — this graph tier is not symbol-level.";
      controls.appendChild(note);
      return;
    }
    let asm;
    try {
      asm = await getJSON(rp("/assessments?limit=50"));
    } catch (e) {
      return;
    }
    if (!asm.items || !asm.items.length) return;

    const toggle = el("div", "overlay-toggle");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "Graph colouring");
    const bStruct = el("button", "graph-btn" + (graphState.overlay === "structure" ? " active" : ""), "Structure");
    const bRisk = el("button", "graph-btn" + (graphState.overlay === "risk" ? " active" : ""), "Risk");
    [bStruct, bRisk].forEach(function (b) { b.setAttribute("type", "button"); });
    bStruct.addEventListener("click", function () { setOverlay("structure", bStruct, bRisk); });
    bRisk.addEventListener("click", function () { setOverlay("risk", bStruct, bRisk); });
    toggle.appendChild(bStruct);
    toggle.appendChild(bRisk);
    controls.appendChild(toggle);

    const decisionById = {};
    asm.items.forEach(function (i) { decisionById[i.assessment_id] = i.decision; });
    if (!graphState.assessmentId || !(graphState.assessmentId in decisionById)) {
      graphState.assessmentId = asm.items[0].assessment_id;
    }
    const picker = sel(
      "risk assessment",
      graphState.assessmentId,
      asm.items.map(function (i) { return [i.assessment_id, i.assessment_id + " · " + (i.decision || "?")]; }),
      function (v) { graphState.assessmentId = v; loadAssessmentRisk(v, decisionById[v]); }
    );
    controls.appendChild(picker);

    const cap = el("p", "chart-note risk-caption");
    cap.hidden = true;
    controls.appendChild(cap);
    graphState.riskCaption = cap;

    // Preload the selected assessment's binding so toggling to Risk is instant.
    await loadAssessmentRisk(graphState.assessmentId, decisionById[graphState.assessmentId]);
    if (graphState.riskLegend) graphState.riskLegend.hidden = graphState.overlay !== "risk";
  }

  function riskEligibleMode(mode) {
    return mode === "symbol" || mode === "godmap";
  }

  function setOverlay(mode, bStruct, bRisk) {
    graphState.overlay = mode;
    if (bStruct) bStruct.classList.toggle("active", mode === "structure");
    if (bRisk) bRisk.classList.toggle("active", mode === "risk");
    if (graphState.riskLegend) graphState.riskLegend.hidden = mode !== "risk";
    applyOverlay();
    updateRiskCaption();
  }

  function normalizeDecision(decision) {
    const d = String(decision || "").toLowerCase();
    if (d === "block") return "reject";
    return RISK_DECISIONS.indexOf(d) >= 0 ? d : "other";
  }
  function normPath(p) { return p == null ? null : String(p).replace(/\\/g, "/"); }

  async function loadAssessmentRisk(assessmentId, decision) {
    const seq = ++riskSeq;  // rapid picker changes must not let an older response overwrite a newer one
    let detail;
    try {
      detail = await getJSON(rp("/assessments/" + encodeURIComponent(assessmentId)));
    } catch (e) {
      if (seq !== riskSeq) return;
      graphState.risk = null;
      applyOverlay();
      updateRiskCaption();
      return;
    }
    if (seq !== riskSeq) return;  // superseded by a later selection
    const content = detail.content || {};
    const scores = content.scores || {};
    const sse = scores.symbol_scope_evidence || {};
    const fanin = sse.symbol_fanin || {};
    const qns = fanin.resolved_qualified_names || [];
    const paths = (fanin.resolved_file_paths || []).map(normPath);
    graphState.risk = {
      assessmentId: assessmentId,
      // `content` stores the decision as `content.decision`; the list-supplied value is preferred.
      decision: normalizeDecision(decision != null ? decision : content.decision),
      qnSet: new Set(qns),
      pathSet: new Set(paths),
      scores: {
        expected_loss: scores.expected_loss,
        benefit: scores.benefit,
        expected_utility: scores.expected_utility,
        rau: scores.rau,
        edit_confidence: scores.edit_confidence,
        // Producer shape (assessment_builder): symbol_fanin.percentile, with a top-level fallback.
        symbol_fan_in_percentile:
          fanin.percentile != null ? fanin.percentile : sse.symbol_fan_in_percentile,
        matched: 0,
      },
    };
    applyOverlay();
    updateRiskCaption();
  }

  function nodeBound(risk, d) {
    return risk.qnSet.has(d.qualified_name)
      && (risk.pathSet.size === 0 || risk.pathSet.has(normPath(d.file_path)));
  }

  function applyOverlay() {
    const cy = graphState.cy;
    if (!cy) return;
    const all = RISK_DECISIONS.concat(["other"]).map(function (d) { return "rb-" + d; }).concat(["rb-unmatched"]);
    cy.nodes().removeClass(all.join(" "));
    if (graphState.overlay !== "risk" || !graphState.risk) return;  // structure mode → kind colours show
    const risk = graphState.risk;
    const cls = "rb-" + risk.decision;
    let matched = 0;
    cy.nodes().forEach(function (n) {
      if (nodeBound(risk, n.data())) { n.addClass(cls); matched += 1; } else { n.addClass("rb-unmatched"); }
    });
    risk.scores.matched = matched;
  }

  function updateRiskCaption() {
    const cap = graphState.riskCaption;
    if (!cap) return;
    if (graphState.overlay !== "risk" || !graphState.risk || !graphState.cy) { cap.hidden = true; return; }
    const r = graphState.risk;
    cap.hidden = false;
    cap.textContent = "Risk overlay: " + r.assessmentId + " · " + r.decision + " · "
      + r.scores.matched + " of " + graphState.cy.nodes().length
      + " nodes bound — assessment-aggregate scope, not per-symbol calibrated risk.";
  }

  function buildRiskLegend(l) {
    RISK_DECISIONS.forEach(function (d) { l.appendChild(swatchLabel(DECISION_STYLE[d].color, d)); });
    l.appendChild(swatchLabel("#3a4753", "not assessed"));
  }

  function buildGraphControls(controls, searchResults, inspector, cyEl) {
    const search = document.createElement("input");
    search.className = "graph-search";
    search.type = "text";
    search.setAttribute("aria-label", "Search graph nodes by symbol, file or kind");
    search.placeholder = "Search symbol / file / kind…";
    search.addEventListener("input", function () { graphSearch(search.value, searchResults, inspector); });
    controls.appendChild(search);

    // Keep exact labels Grid/Circle/… for e2e pins; Auto is one-shot smart pick (not a tour).
    const layouts = el("div", "layout-group");
    layouts.setAttribute("role", "group");
    layouts.setAttribute("aria-label", "Graph layout");
    [["Auto", "auto"], ["Grid", "grid"], ["Circle", "circle"], ["Concentric", "concentric"], ["CoSE", "cose"]]
      .forEach(function (pair) {
        const b = el("button", "graph-btn", pair[0]);
        b.setAttribute("type", "button");
        b.addEventListener("click", function () { runGraphLayout(pair[1]); });
        layouts.appendChild(b);
      });
    controls.appendChild(layouts);

    const zoom = el("div", "zoom-group");
    zoom.setAttribute("role", "group");
    zoom.setAttribute("aria-label", "Graph zoom");
    [
      ["Fit", "fit", "Fit graph to viewport"],
      ["−", "out", "Zoom out"],
      ["+", "in", "Zoom in"],
      ["100%", "reset", "Reset zoom to 100%"],
    ].forEach(function (pair) {
      const b = el("button", "graph-btn", pair[0]);
      b.setAttribute("type", "button");
      b.setAttribute("aria-label", pair[2]);
      b.setAttribute("title", pair[2]);
      b.addEventListener("click", function () {
        if (pair[1] === "fit") runGraphLayout("fit");
        else runGraphZoom(pair[1]);
      });
      zoom.appendChild(b);
    });
    controls.appendChild(zoom);

    if (cyEl && typeof cyEl.requestFullscreen === "function") {
      const fs = el("button", "graph-btn", "Fullscreen");
      fs.setAttribute("type", "button");
      fs.setAttribute("aria-label", "Enter graph fullscreen");
      fs.addEventListener("click", function () {
        if (document.fullscreenElement === cyEl) {
          document.exitFullscreen().catch(function () { /* ignore */ });
        } else {
          cyEl.requestFullscreen().catch(function () { /* ignore denied */ });
        }
      });
      cyEl.addEventListener("fullscreenchange", function () {
        const active = document.fullscreenElement === cyEl;
        fs.textContent = active ? "Exit fullscreen" : "Fullscreen";
        fs.setAttribute("aria-label", active ? "Exit graph fullscreen" : "Enter graph fullscreen");
        window.requestAnimationFrame(function () {
          if (graphState.cy) {
            graphState.cy.resize();
            graphState.cy.fit(undefined, 30);
          }
        });
      });
      controls.appendChild(fs);
    }
  }

  function graphSearch(query, searchResults, inspector) {
    const cy = graphState.cy;
    clear(searchResults);
    if (!cy) return;
    cy.nodes().removeClass("search-hit search-dim");
    cy.edges().removeClass("search-dim");
    const q = (query || "").trim().toLowerCase();
    if (q.length < 2) return;
    const hits = cy.nodes().filter(function (n) {
      const d = n.data();
      return [d.label, d.qualified_name, d.file_path, d.kind].some(
        function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
    });
    if (hits.length === 0) {
      searchResults.appendChild(el("p", "chart-note", "No matches."));
      return;
    }
    cy.elements().addClass("search-dim");
    hits.removeClass("search-dim").addClass("search-hit");
    hits.connectedEdges().removeClass("search-dim");
    searchResults.appendChild(el("p", "chart-note", hits.length + " match(es)" + (hits.length > 30 ? " (showing 30)" : "")));
    hits.toArray().slice(0, 30).forEach(function (n) {
      const d = n.data();
      const row = el("button", "search-row", d.qualified_name || d.label || d.id);
      row.setAttribute("type", "button");
      row.addEventListener("click", function () {
        cy.animate({ fit: { eles: n, padding: 120 }, duration: 300 });
        cy.$(":selected").unselect();
        n.select();
        showInspector(inspector, d, true);  // keyboard path: move focus to the details region
      });
      searchResults.appendChild(row);
    });
  }

  function runGraphLayout(name) {
    const cy = graphState.cy;
    if (!cy) return;
    if (name === "fit") { cy.fit(undefined, 30); return; }
    // Capture the auto-click intent BEFORE resolving to a concrete layout, so only an explicit Auto
    // click animates — the initial/live-refresh path goes through layoutFor and never reaches here.
    const isAutoClick = name === "auto";
    if (isAutoClick) {
      // Deterministic one-shot pick from current graph size — never auto-cycles layouts.
      name = layoutNameFor(cy.nodes().length);
    }
    if (name === "cose" && cy.nodes().length > 300) {
      if (!window.confirm("Force layout on " + cy.nodes().length + " nodes may be slow. Continue?")) return;
    }
    const cheap = name === "grid" || name === "circle" || name === "concentric";
    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const opts = name === "cose"
      ? coseOptions(cy.nodes().length, false, isAutoClick && !reduceMotion)
      : {
          name: name,
          animate: cheap && !reduceMotion,
          animationDuration: 280,
          fit: false,
          padding: 30,
        };
    const layout = cy.layout(opts);
    layout.one("layoutstop", function () {
      cy.fit(undefined, 30);
      updateSymbolLabelVisibility();  // re-sync zoom-gated symbol labels after the (possibly animated) fit
    });
    layout.run();
  }

  function runGraphZoom(action) {
    const cy = graphState.cy;
    if (!cy) return;
    if (action === "reset") {
      cy.zoom(1);
      cy.center();
      return;
    }
    const factor = action === "in" ? 1.25 : 1 / 1.25;
    const next = Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * factor));
    const sel = cy.$("node:selected");
    if (sel.nonempty()) {
      cy.zoom({ level: next, position: sel.position() });
    } else {
      cy.zoom(next);
    }
  }

  function showInspector(inspector, d, focus) {
    if (!inspector) return;
    clear(inspector);
    const degree = d.degree != null ? d.degree : (d.symbol_count != null ? d.symbol_count : null);
    const rows = [
      ["symbol", d.qualified_name || d.label || d.id],
      ["kind", d.kind || "—"],
      ["file", d.file_path || "—"],
      ["fan-in", d.inbound != null ? String(d.inbound) : "—"],
      ["fan-out", d.outbound != null ? String(d.outbound) : "—"],
      ["degree", degree != null ? String(degree) : "—"],
    ];
    rows.forEach(function (kv) { inspector.appendChild(inspRow(kv[0], kv[1])); });
    if (graphState.overlay === "risk" && graphState.risk) appendRiskDetail(inspector, d);
    if (graphState.learning) appendLearningDetail(inspector, d);
    if (focus) inspector.focus();
  }

  function inspRow(key, value) {
    const r = el("div", "insp-row");
    r.appendChild(el("span", "insp-key", key));
    r.appendChild(el("span", "insp-val", value));  // el() sets textContent, never raw markup
    return r;
  }

  function appendRiskDetail(inspector, d) {
    const r = graphState.risk;
    inspector.appendChild(el("div", "insp-sep"));
    if (!nodeBound(r, d)) {
      inspector.appendChild(inspRow("risk", "not part of assessment " + r.assessmentId));
      return;
    }
    const sc = r.scores;
    const num = (x, dp) => (x == null || Number.isNaN(Number(x)) ? "—" : Number(x).toFixed(dp));
    const pct = (x) => (x == null || Number.isNaN(Number(x)) ? "—" : (Number(x) * 100).toFixed(0) + "%");
    // Decision (categorical) is the visual signal. The magnitudes below are shown as NUMBERS only —
    // never colour gradients — and expected loss stays in loss points, not a percentage.
    [
      ["decision", r.decision],
      ["expected loss", sc.expected_loss == null ? "—" : num(sc.expected_loss, 2) + " loss pts"],
      ["benefit", pct(sc.benefit)],
      ["expected utility", num(sc.expected_utility, 3)],
      ["RAU", num(sc.rau, 3)],
      ["edit confidence", num(sc.edit_confidence, 2)],
      ["fan-in percentile", pct(sc.symbol_fan_in_percentile)],
    ].forEach(function (kv) { inspector.appendChild(inspRow(kv[0], kv[1])); });
    inspector.appendChild(el("p", "insp-note",
      "Risk scope: assessment aggregate. This is not per-symbol calibrated risk."));
  }

  function showGraphUnavailable(graphCard, message) {
    graphCard.classList.add("graph-unavailable");
    graphCard.appendChild(message);
    destroyCy();
  }

  async function loadFullGraph(cyEl, graphCard) {
    const seq = ++graphSeq;
    graphCard.querySelectorAll(".chart-note, .empty.warn").forEach((n) => n.remove());
    let g;
    try {
      g = await getJSON(rp("/graph/full"));
    } catch (e) {
      // Destroy the prior instance too: a failed refresh (e.g. LIVE polling) must not leak the last
      // Cytoscape/WebGL context on a now-detached container until the next successful render.
      if (seq === graphSeq) {
        showGraphUnavailable(graphCard, fallback("codebase graph unavailable"));
      }
      return;
    }
    if (seq !== graphSeq) return;
    if (!g.available) { showGraphUnavailable(graphCard, fallback(g)); return; }
    if (!g.nodes.length) {
      showGraphUnavailable(
        graphCard,
        emptyMsg(g.fallback_reason || "No structural nodes in the current graph."),
      );
      return;
    }
    renderCy(cyEl, g);
    const bits = [
      g.mode === "file" ? "Collapsed file graph" : "Symbol graph",
      g.nodes.length + " node(s)",
      g.edges.length + " edge(s)",
    ];
    if (g.mode === "file") {
      const totalFiles = g.total_file_count != null ? g.total_file_count : g.total_node_count;
      if (g.truncated || g.collapsed || totalFiles !== g.nodes.length) {
        bits.push("Showing " + g.nodes.length + " of " + totalFiles + " files");
      }
    } else if (g.truncated || g.collapsed || g.total_node_count !== g.nodes.length) {
      bits.push("Showing " + g.nodes.length + " of " + g.total_node_count + " nodes");
    }
    graphCard.appendChild(el("p", "chart-note", bits.join(" · ")));
  }

  async function loadGodNodeMap(cyEl, graphCard) {
    const seq = ++graphSeq;
    graphCard.querySelectorAll(".chart-note, .empty.warn").forEach((n) => n.remove());
    let g;
    try {
      g = await getJSON(rp("/graph/godmap"));
    } catch (e) {
      if (seq === graphSeq) {
        showGraphUnavailable(graphCard, fallback("god-node map unavailable"));
      }
      return;
    }
    if (seq !== graphSeq) return;
    if (!g.available) { showGraphUnavailable(graphCard, fallback(g)); return; }
    if (!g.nodes.length) {
      showGraphUnavailable(
        graphCard,
        emptyMsg(g.fallback_reason || "No hot files in the current graph."),
      );
      return;
    }
    renderCy(cyEl, g);
    const hubs = g.nodes.filter((n) => n.graph_role === "hub").length;
    const symbols = g.nodes.filter((n) => n.graph_role === "symbol").length;
    const bits = ["God-node map", hubs + " file hub(s)", symbols + " symbol(s)", g.edges.length + " link(s)"];
    if (g.truncated || g.total_file_count !== hubs) {
      bits.push("Showing top " + hubs + " of " + g.total_file_count + " fan-in files");
    }
    graphCard.appendChild(el("p", "chart-note", bits.join(" · ")));
  }

  function degreeOf(n) {
    if (n.graph_role === "symbol" && n.inbound_count != null) return n.inbound_count;
    if (n.degree != null) return n.degree;
    if (n.inbound_count != null) return n.inbound_count;
    if (n.symbol_count != null) return n.symbol_count;
    return 0;
  }

  function renderCy(container, g) {
    destroyCy();
    const graphCard = container.closest(".card");
    if (graphCard) graphCard.classList.remove("graph-unavailable");
    const maxDeg = g.nodes.reduce((m, n) => Math.max(m, degreeOf(n)), 1);
    const elements = [];
    g.nodes.forEach((n) => {
      elements.push({ group: "nodes", data: {
        id: n.id,
        label: n.label != null ? n.label : n.id,
        kind: n.kind || "unknown",
        // Files vs symbols drives the two-colour fill; hubs keep their god-map rainbow (is_file false).
        is_file: n.graph_role !== "hub" && (n.kind === "file" || n.kind === "file_hub"),
        qualified_name: n.qualified_name || null,
        file_path: n.file_path || null,
        graph_role: n.graph_role || null,
        shape: n.shape || "ellipse",
        hub_rank: n.hub_rank != null ? n.hub_rank : null,
        hub_color: n.hub_rank != null ? HUB_COLORS[n.hub_rank % HUB_COLORS.length] : null,
        degree: n.degree != null ? n.degree : null,
        inbound: n.inbound_count != null ? n.inbound_count : null,
        outbound: n.outbound_count != null ? n.outbound_count : null,
        symbol_count: n.symbol_count != null ? n.symbol_count : null,
        size: 12 + 24 * Math.sqrt(degreeOf(n) / maxDeg),
      } });
    });
    g.edges.forEach((e, i) => {
      elements.push({ group: "edges", data: {
        id: "e" + i,  // guaranteed-unique id; the graph may hold parallel source->target edges
        source: e.source, target: e.target,
        kind: e.kind || "", weight: e.weight != null ? e.weight : 1,
        edge_type: e.edge_type || "cross_symbol",
        line_style: e.line_style || "solid",
        spoke_color: e.hub_rank != null ? SPOKE_COLORS[e.hub_rank % SPOKE_COLORS.length] : null,
      } });
    });
    // God maps keep file-hub labels visible but progressively disclose their much denser symbol labels.
    const showLabels = g.mode !== "godmap" && (g.mode === "file" || g.nodes.length <= 250);
    graphState.cy = makeCy(container, elements, layoutFor(g.nodes.length), cyStyle(showLabels));
    if (typeof ResizeObserver === "function") {
      graphState.resizeObserver = new ResizeObserver(function () {
        if (graphState.cy) graphState.cy.resize();
      });
      graphState.resizeObserver.observe(container);
    }
    graphState.mode = g.mode;
    graphState.symbolLabelsVisible = null;
    graphState.cy.on("zoom", updateSymbolLabelVisibility);
    updateSymbolLabelVisibility();
    exposeGraphForDev();
    if (graphState.inspector) {
      clear(graphState.inspector);
      graphState.inspector.appendChild(el("p", "chart-note", "Select a node (or a search result) to inspect it."));
      graphState.cy.on("tap", "node", function (evt) { showInspector(graphState.inspector, evt.target.data(), false); });
    }
    if (graphState.overlay === "risk" && graphState.risk) applyOverlay();  // survive a re-render
    if (graphState.learning) applyLearningBadges();
  }

  function makeCy(container, elements, layout, style) {
    const base = {
      container: container, elements: elements, layout: layout, style: style,
      // Prefer default wheel zoom (custom sensitivity is OS/hardware inconsistent).
      minZoom: 0.08, maxZoom: 3.5,
      textureOnViewport: true, pixelRatio: 1,
      // Single-click selection is the intended UX and avoids the drag-time box-select overlay. (The one
      // residual CSP note is Cytoscape's injected container-position <style>, neutralised in style.css.)
      boxSelectionEnabled: false, selectionType: "single",
    };
    try {
      return cytoscape(Object.assign({ renderer: { name: "canvas", webgl: true } }, base));
    } catch (e) {
      return cytoscape(base);  // fall back to the plain canvas renderer if WebGL init fails
    }
  }

  function layoutNameFor(nodeCount) {
    // Smart Auto: force layout only while cheap; large graphs stay on deterministic grid.
    return nodeCount <= 300 ? "cose" : "grid";
  }

  function coseOptions(nodeCount, fit, animate) {
    // Longer ideal edges + wider component spacing on smaller graphs keep the map from collapsing to a
    // central cluster. Caps prevent one- and two-node graphs from generating multi-thousand-pixel values.
    // `animate` is opt-in (explicit Auto click only); the initial/live-refresh path leaves it falsy.
    const n = Math.max(1, nodeCount);
    return {
      name: "cose", animate: !!animate, animationDuration: animate ? 600 : undefined, fit: fit, padding: 30,
      nodeRepulsion: 8000, numIter: 400,
      idealEdgeLength: Math.round(Math.min(160, 80 + 6000 / n)),
      componentSpacing: Math.round(Math.min(180, Math.max(80, 12000 / n))),
      gravity: n <= 150 ? 0.45 : 0.8,
    };
  }

  function layoutFor(nodeCount) {
    const name = layoutNameFor(nodeCount);
    if (name === "cose") return coseOptions(nodeCount, true);
    return { name: "grid", fit: true, padding: 30 };
  }

  function cyStyle(showLabels) {
    const s = [
      { selector: "node", style: {
        "background-color": SYMBOL_COLOR,
        "width": "data(size)", "height": "data(size)",
        "shape": "data(shape)",
        "label": showLabels ? "data(label)" : "",
        "font-size": 11, "color": "#c9d1d9",
        "text-valign": "center", "text-halign": "right", "text-margin-x": 4,
        "min-zoomed-font-size": 10,
      } },
      { selector: "edge", style: {
        "width": 1, "line-color": "#3a4753", "opacity": 0.5,
        "line-style": "data(line_style)",
        "curve-style": "straight", "target-arrow-shape": "none",  // WebGL supports straight, not bezier
      } },
      { selector: 'node[graph_role="hub"]', style: {
        "shape": "round-rectangle",
        "background-color": "data(hub_color)",
        "border-width": 2, "border-color": "#e6edf3", "border-opacity": 0.55,
        "label": "data(label)",
        "font-size": 12, "font-weight": "bold", "text-valign": "center", "text-halign": "center",
        "text-max-width": 72, "text-wrap": "wrap",
      } },
      { selector: 'node[graph_role="symbol"]', style: {
        "border-width": 1, "border-color": "#0d1117", "border-opacity": 0.9,
      } },
      { selector: 'node[graph_role="symbol"].show-symbol-label', style: {
        "label": "data(label)",
      } },
      { selector: 'edge[edge_type="spoke"]', style: {
        "line-color": "data(spoke_color)",
        "width": 1.5,
        "opacity": 0.42,
        "line-style": "dashed",
      } },
      { selector: 'edge[edge_type="cross_symbol"]', style: {
        "line-color": "#8b949e",
        "width": 1.2,
        "opacity": 0.72,
        "line-style": "solid",
      } },
      { selector: "node:selected", style: {
        "border-width": 2, "border-color": "#ffd24d", "border-opacity": 1,
        "label": "data(label)", "font-weight": "bold",
      } },
      { selector: "node.search-dim", style: { "opacity": 0.15 } },
      { selector: "edge.search-dim", style: { "opacity": 0.06 } },
      { selector: "node.search-hit", style: {
        "border-width": 3, "border-color": "#ffd24d", "border-opacity": 1, "opacity": 1,
        "label": "data(label)", "font-weight": "bold",
      } },
    ];
    // Files read neutral over the violet symbol base. This precedes the risk rules, so the decision
    // overlay still wins; hubs are is_file=false and retain their established god-map colours.
    s.push({ selector: "node[?is_file]", style: { "background-color": FILE_COLOR } });
    // Risk overlay (appended last so rb-* classes override the kind colours when risk view is on).
    Object.keys(DECISION_STYLE).forEach((k) => {
      s.push({ selector: "node.rb-" + k, style: {
        "background-color": DECISION_STYLE[k].color, "shape": DECISION_STYLE[k].shape,
        "border-width": 3, "border-color": "#e6edf3", "border-opacity": 0.9,
      } });
    });
    s.push({ selector: "node.rb-unmatched", style: {
      "background-color": "#3a4753", "shape": "ellipse", "opacity": 0.35, "border-width": 0,
    } });
    s.push({ selector: 'node.rb-unmatched[graph_role="hub"]', style: {
      "shape": "round-rectangle",
      "border-width": 2, "border-color": "#e6edf3", "border-opacity": 0.35,
    } });
    // Verified-lesson badge (appended last so it owns the border in either overlay mode). The node's
    // fill/shape still conveys kind or decision; this ring only marks "a verified lesson touches this".
    s.push({ selector: "node.has-lesson", style: {
      "border-width": 4, "border-color": "#f778ba", "border-style": "double", "border-opacity": 1,
    } });
    return s;
  }

  function updateSymbolLabelVisibility() {
    const cy = graphState.cy;
    if (!cy || graphState.mode !== "godmap") return;
    const visible = cy.zoom() >= SYMBOL_LABEL_ZOOM;
    if (graphState.symbolLabelsVisible === visible) return;
    graphState.symbolLabelsVisible = visible;
    const symbols = cy.nodes('[graph_role="symbol"]');
    if (visible) symbols.addClass("show-symbol-label");
    else symbols.removeClass("show-symbol-label");
  }

  function exposeGraphForDev() {
    if (!DEV_MODE) return;
    window.__pebraGraph = {
      snapshot: function () {
        const cy = graphState.cy;
        if (!cy) return { mode: graphState.mode, nodes: [], edges: [] };
        return {
          mode: graphState.mode,
          zoom: cy.zoom(),
          nodes: cy.nodes().map(function (n) {
            return {
              id: n.id(),
              graph_role: n.data("graph_role"),
              classes: n.classes(),
              shape: n.renderedStyle("shape"),
              label: n.renderedStyle("label"),
              width: Math.round(n.width() * 1000) / 1000,
              height: Math.round(n.height() * 1000) / 1000,
            };
          }),
          edges: cy.edges().map(function (e) {
            return {
              source: e.data("source"),
              target: e.data("target"),
              edge_type: e.data("edge_type"),
              line_style: e.renderedStyle("line-style"),
            };
          }),
        };
      },
    };
  }

  function destroyCy() {
    if (graphState.resizeObserver) {
      graphState.resizeObserver.disconnect();
      graphState.resizeObserver = null;
    }
    if (graphState.cy) {
      try { graphState.cy.destroy(); } catch (e) { /* already torn down */ }
      graphState.cy = null;
    }
    graphState.symbolLabelsVisible = null;
  }

  function fallback(info) {
    const reason = typeof info === "string" ? info : (info && info.fallback_reason);
    const setup = typeof info === "object" && info ? info.setup_command : null;
    const hint = typeof info === "object" && info ? info.setup_hint : null;
    const wrap = el("div", "empty warn");
    wrap.appendChild(el("p", null, "Graph unavailable — " + (reason || "no codegraph index")));
    wrap.appendChild(el("p", null, "Graph setup: " + (hint || "Initialize or repair the local CodeGraph index, then refresh this tab.")));
    wrap.appendChild(el("code", null, setup || "pebra setup-graph --fix --repo-root ."));
    return wrap;
  }
  function graphLegend() {
    const l = el("div", "graph-legend");
    l.appendChild(swatchLabel(SYMBOL_COLOR, "symbol"));
    l.appendChild(swatchLabel(FILE_COLOR, "file"));
    l.appendChild(swatchLabel(HUB_COLORS[0], "god-node file hub"));
    l.appendChild(swatchLabel(SPOKE_COLORS[0], "file → symbol spoke"));
    l.appendChild(swatchLabel("#8b949e", "symbol cross-link"));
    return l;
  }
  function swatchLabel(color, text) {
    const s = el("span", null, text);
    const sw = el("span", "swatch"); sw.style.background = color; s.prepend(sw);
    return s;
  }

  // ---- uPlot helpers ----
  const AXIS_OPTS = { stroke: AXIS, grid: { stroke: GRID }, ticks: { stroke: GRID } };
  let chartInstance = null;
  function newChart(box, opts, data) {
    clear(box);
    if (chartInstance) { try { chartInstance.destroy(); } catch (e) {} chartInstance = null; }
    const w = box.clientWidth || 600;
    chartInstance = new uPlot(Object.assign({ width: w, height: 300 }, opts), data, box);
  }
  function drawSeries(box, items) {
    if (!items.length) { box.appendChild(emptyMsg("No score history yet. It appears after your first pebra assess run.")); return; }
    const ordered = items.slice().reverse(); // oldest -> newest
    const xs = ordered.map((_, i) => i + 1);
    const risk = ordered.map((i) => i.scores.expected_loss);
    const benefit = ordered.map((i) => i.scores.benefit);
    const utility = ordered.map((i) => i.scores.expected_utility);
    newChart(box, {
      scales: { x: { time: false } },
      axes: [AXIS_OPTS, AXIS_OPTS],
      series: [
        { label: "#" },
        { label: "risk (expected loss)", stroke: RISK, width: 2 },
        { label: "benefit", stroke: BENEFIT, width: 2 },
        { label: "expected utility", stroke: UTILITY, width: 2 },
      ],
    }, [xs, risk, benefit, utility]);
  }
  function drawReliability(box, bins) {
    const used = bins.filter((b) => b.count > 0);
    const xs = used.map((b) => b.mean_predicted);
    const ys = used.map((b) => b.observed_rate);
    newChart(box, {
      scales: { x: { time: false, range: [0, 1] }, y: { range: [0, 1] } },
      axes: [AXIS_OPTS, AXIS_OPTS],
      series: [
        { label: "predicted" },
        { label: "observed", stroke: ACCENT, width: 2, points: { show: true, size: 7 } },
        { label: "ideal", stroke: AXIS, width: 1, dash: [6, 6] },
      ],
    }, [xs, ys, xs]);
  }
  function drawScatter(box, points) {
    const xs = points.map((p) => p.predicted);
    const ys = points.map((p) => p.actual);
    newChart(box, {
      scales: { x: { time: false } },
      axes: [AXIS_OPTS, AXIS_OPTS],
      series: [
        { label: "predicted" },
        { label: "actual", stroke: BENEFIT, width: 0, points: { show: true, size: 6 } },
      ],
    }, [xs, ys]);
  }

  // ---- small DOM helpers ----
  function headRow(cols) {
    const thead = el("thead"); const tr = el("tr");
    cols.forEach((c) => {
      if (typeof c === "string") {
        tr.appendChild(el("th", null, c));
      } else {
        tr.appendChild(el("th", c.cls || null, c.label));
      }
    });
    thead.appendChild(tr); return thead;
  }
  function cell(text, cls) { return el("td", cls || null, text == null ? "—" : String(text)); }
  function sel(label, value, options, onChange) {
    const s = document.createElement("select");
    s.setAttribute("aria-label", label);
    options.forEach(([v, t]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = t; if (v === value) o.selected = true;
      s.appendChild(o);
    });
    s.addEventListener("change", () => onChange(s.value));
    return s;
  }
  function rp(suffix) { return "/api/repos/" + encodeURIComponent(repo) + suffix; }

  // ---- router ----
  const TAB_ELEMENTS = Array.from(document.querySelectorAll(".tab[data-tab]"));
  const TABS = TAB_ELEMENTS.map((a) => a.dataset.tab);
  const RENDER = {
    graph: renderGraph,
    activity: renderActivity,
    calibration: renderCalibration,
    learning: renderLearning,
  };
  // Legacy hashes still leave the Graph tab (and destroy WebGL) without 404ing bookmarks.
  function resolveTab(hash) {
    const h = (hash || "").replace("#", "");
    if (h === "overview" || h === "history") return "activity";
    if (TABS.indexOf(h) >= 0) return h;
    return "graph";  // product default: codebase graph first
  }
  function currentTab() { return resolveTab(location.hash); }
  let routing = false;
  function renderSkeleton(view) {
    clear(view);
    const group = el("div", "skeleton-group");
    for (let i = 0; i < 3; i++) {
      const block = el("div", "skeleton-block");
      block.setAttribute("aria-hidden", "true");  // decorative placeholder, not content
      group.appendChild(block);
    }
    view.appendChild(group);
  }

  async function route() {
    if (routing) return;
    routing = true;
    const tab = currentTab();
    TAB_ELEMENTS.forEach((t) => {
      const on = t.getAttribute("data-tab") === tab;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.setAttribute("tabindex", on ? "0" : "-1");
    });
    TABS.forEach((t) => { document.getElementById("view-" + t).hidden = t !== tab; });
    // Release the WebGL graph instance whenever the Graph tab is not the active one. The graphSeq
    // guard can't catch a switch-away-during-fetch (route() serialises renders, so no competing
    // loadFullGraph bumps the seq), and nothing else tears the instance down off-tab; this does.
    if (tab !== "graph") destroyCy();
    const view = document.getElementById("view-" + tab);
    view.removeAttribute("data-loaded");
    // Skeleton only on a tab's FIRST load: not the 1.5s live refresh (tabEverLoaded is already set by
    // then), and a no-op on tabs that paint their shell synchronously (the hasChildNodes guard).
    const firstLoad = repo && !tabEverLoaded.has(tab);
    let skeletonTimer = null;
    if (firstLoad) {
      skeletonTimer = setTimeout(function () {
        if (!view.hasChildNodes()) renderSkeleton(view);
      }, 150);
    }
    try {
      if (!repo) { view.hidden = false; clear(view); view.appendChild(emptyMsg("No repo selected (append &repo=<id> to the URL).")); }
      else { await RENDER[tab](view); tabEverLoaded.add(tab); }
      view.setAttribute("data-loaded", "true");
    } catch (e) {
      clear(view); view.appendChild(emptyMsg("Error loading " + tab + ": " + e.message));
    } finally {
      if (skeletonTimer) clearTimeout(skeletonTimer);
      routing = false;
      // If the hash changed while this render was in flight, the dropped hashchange won't re-fire for
      // the same hash — re-run so the UI can't get stranded on the previous tab (e.g. a fast double-click).
      if (currentTab() !== tab) route();
    }
  }

  async function refreshLiveView() {
    const view = document.getElementById("view-" + currentTab());
    if (view.contains(document.activeElement)) return;
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    await route();
    window.scrollTo(scrollX, scrollY);
  }

  TAB_ELEMENTS.forEach(function (tab, index) {
    tab.addEventListener("keydown", function (event) {
      let next = null;
      if (event.key === "ArrowRight") next = (index + 1) % TAB_ELEMENTS.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + TAB_ELEMENTS.length) % TAB_ELEMENTS.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = TAB_ELEMENTS.length - 1;
      if (next == null) return;
      event.preventDefault();
      const target = TAB_ELEMENTS[next];
      target.focus();
      location.hash = "#" + target.dataset.tab;
    });
  });

  window.addEventListener("hashchange", route);

  async function start() {
    repoChip.textContent = repo ? "repo " + repo : "no repo";
    if (LIVE) { liveDot.hidden = false; }
    if (boot) boot.remove();
    await refreshChain();
    await route();
    if (LIVE) {
      setInterval(() => { refreshChain(); refreshLiveView(); }, LIVE_MS);
    }
  }
  start();
})();
