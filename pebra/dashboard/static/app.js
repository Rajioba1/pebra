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
  const boot = document.getElementById("boot");
  const liveDot = document.getElementById("live-dot");
  const repoChip = document.getElementById("repo-chip");
  const chainPill = document.getElementById("chain-pill");
  let graphSeq = 0;

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

  // ---- Overview ----
  async function renderOverview(view) {
    const [overview, series, chain] = await Promise.all([
      getJSON(rp("/overview")), getJSON(rp("/scores-series?limit=500")), refreshChain(),
    ]);
    clear(view);
    const confs = series.items.map((i) => i.scores.edit_confidence).filter((x) => x != null);
    const meanConf = confs.length ? confs.reduce((a, b) => a + b, 0) / confs.length : null;
    const proceed = overview.by_decision.proceed || 0;

    const row = el("div", "grid stat-row");
    row.appendChild(stat("Assessments run", String(overview.total)));
    row.appendChild(stat("Proceed rate", overview.total ? pct(proceed / overview.total) : "—",
      proceed + " of " + overview.total));
    row.appendChild(stat("Mean edit-confidence", meanConf == null ? "—" : fmt(meanConf, 2)));
    const counts = (chain && chain.counts) || {};
    row.appendChild(stat("Learned rules", String(counts["learned_" + "risk_" + "facts"] || 0),
      chain && chain.valid ? "chain valid" : "chain BROKEN"));
    view.appendChild(row);

    const dcard = card("Decisions");
    dcard.appendChild(decisionBar(overview.by_decision, overview.total));
    view.appendChild(dcard);

    // Audit chain, in human terms.
    const acard = card("Audit chain");
    const list = el("div", "dist-legend");
    Object.keys(chainLabels).forEach((k) => {
      list.appendChild(el("span", null, (chainLabels[k]) + ": " + (counts[k] || 0)));
    });
    acard.appendChild(list);
    view.appendChild(acard);
  }

  function decisionBar(byDecision, total) {
    const wrap = el("div");
    const bar = el("div", "distbar");
    const legend = el("div", "dist-legend");
    Object.keys(byDecision).forEach((d) => {
      const n = byDecision[d];
      const seg = el("span");
      seg.style.width = (total ? (100 * n / total) : 0) + "%";
      seg.style.background = RAMP[d] || "#5c6773";
      bar.appendChild(seg);
      const item = el("span", null, d + " " + n);
      const sw = el("span", "swatch");
      sw.style.background = RAMP[d] || "#5c6773";
      item.prepend(sw);
      legend.appendChild(item);
    });
    wrap.appendChild(bar);
    wrap.appendChild(legend);
    return wrap;
  }

  // ---- History ----
  const historyState = { assessment_id: null };
  async function renderHistory(view) {
    const [data, series, lessons] = await Promise.all([
      getJSON(rp("/assessments?limit=100")), getJSON(rp("/scores-series?limit=200")),
      getJSON(rp("/learning/context?limit=200")).catch(() => ({ status: "unavailable", items: [] })),
    ]);
    clear(view);
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
    if (!data.items.length) { hcard.appendChild(emptyMsg("No assessments recorded yet.")); }
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
          showMeasuredBenefit(it.assessment_id, bbody);
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
    if (historyState.assessment_id) showMeasuredBenefit(historyState.assessment_id, bbody);
  }

  // Fetch one assessment's detail and render its measured (verify-time) RCA benefit. The measured signal
  // lives on a post_assessment_guardrails row (measured_benefit + measured_benefit_deltas), exposed by
  // GET /api/repos/{repo}/assessments/{id}. Distinct from the assess-time projected `benefit` in the
  // table.
  async function showMeasuredBenefit(id, box) {
    clear(box);
    box.appendChild(el("p", "chart-note", "loading " + id + "…"));
    try {
      const d = await getJSON(rp("/assessments/" + encodeURIComponent(id)));
      const rows = (d.guardrails || []);
      const g = rows.filter(function (x) {
        return x && x.measured_benefit_deltas && Object.keys(x.measured_benefit_deltas).length;
      }).pop();
      clear(box);
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
      box.appendChild(priorTable);
      if (!g || g.measured_benefit == null) {
        box.appendChild(emptyMsg("No verify / measured-benefit recorded for " + id + " yet."));
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
      box.appendChild(t);
    } catch (e) {
      clear(box);
      box.appendChild(emptyMsg("Error loading " + id + ": " + e.message));
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
    if (!data.sample_count) { clear(chartBox); chartBox.appendChild(emptyMsg("No labelled predictions in this scope yet.")); return; }
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
    if (!facts.items.length) fcard.appendChild(emptyMsg("No learned rules yet."));
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
      lcard.appendChild(emptyMsg("No verified completed outcomes have produced recallable lessons yet."));
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
  // Categorical kind palette: dark-legible and deliberately distinct from the green/gold/orange/red
  // risk RAMP so "coloured by kind" can never be misread as "coloured by risk" (risk overlay is M6).
  const KIND_COLORS = {
    function: "#58a6ff", method: "#58a6ff",
    class: "#a78bfa", struct: "#a78bfa", interface: "#a78bfa", trait: "#a78bfa", protocol: "#a78bfa",
    component: "#2dd4bf", route: "#2dd4bf",
    namespace: "#8b949e", module: "#8b949e",
    file: "#6e7681",
    default: "#566173",
  };
  const graphState = { cy: null, mode: null };

  async function renderGraph(view) {
    clear(view);
    const overviewCard = card("Repo hotspots (highest inbound fan-in)");
    view.appendChild(overviewCard);
    try {
      const ov = await getJSON(rp("/graph/overview?top_n=15"));
      if (!ov.available) {
        overviewCard.appendChild(fallback(ov));
      } else if (!ov.files.length) {
        overviewCard.appendChild(emptyMsg("No fan-in in the current graph."));
      } else {
        const t = el("table");
        t.appendChild(headRow(["file", "dependents"]));
        const tb = el("tbody");
        ov.files.forEach((f) => {
          const tr = el("tr");
          tr.appendChild(cell(f.file_path, "mono"));
          tr.appendChild(cell(String(f.distinct_caller_count), "num"));
          tb.appendChild(tr);
        });
        t.appendChild(tb); overviewCard.appendChild(t);
        if (ov.truncated) overviewCard.appendChild(el("p", "chart-note", "top " + ov.files.length + " of " + ov.total_file_count));
      }
    } catch (e) {
      overviewCard.appendChild(fallback("graph overview unavailable"));
    }

    const graphCard = card("Codebase graph");
    graphCard.appendChild(el("div", "controls"));  // search / layout controls land in M5
    const cyEl = el("div", "graph-cy");
    cyEl.id = "graph-cy";
    graphCard.appendChild(cyEl);
    graphCard.appendChild(graphLegend());
    view.appendChild(graphCard);
    await loadFullGraph(cyEl, graphCard);
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
      if (seq === graphSeq) { graphCard.appendChild(fallback("codebase graph unavailable")); destroyCy(); }
      return;
    }
    if (seq !== graphSeq) return;
    if (!g.available) { graphCard.appendChild(fallback(g)); destroyCy(); return; }
    if (!g.nodes.length) {
      graphCard.appendChild(el("p", "chart-note", g.fallback_reason || "No structural nodes in the current graph."));
      destroyCy();
      return;
    }
    renderCy(cyEl, g);
    const bits = [g.nodes.length + " node(s)", g.edges.length + " edge(s)"];
    if (g.mode === "file") bits.push("collapsed to files");
    if (g.truncated) bits.push("showing " + g.nodes.length + " of " + g.total_node_count);
    graphCard.appendChild(el("p", "chart-note", bits.join(" · ")));
  }

  function degreeOf(n) {
    if (n.degree != null) return n.degree;
    if (n.symbol_count != null) return n.symbol_count;
    return 0;
  }

  function renderCy(container, g) {
    destroyCy();
    const maxDeg = g.nodes.reduce((m, n) => Math.max(m, degreeOf(n)), 1);
    const elements = [];
    g.nodes.forEach((n) => {
      elements.push({ group: "nodes", data: {
        id: n.id,
        label: n.label != null ? n.label : n.id,
        kind: n.kind || "unknown",
        qualified_name: n.qualified_name || null,
        file_path: n.file_path || null,
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
      } });
    });
    const showLabels = g.nodes.length <= 250;  // WebGL label atlas is bounded; hover labels come in M5
    graphState.cy = makeCy(container, elements, layoutFor(g.nodes.length), cyStyle(showLabels));
    graphState.mode = g.mode;
  }

  function makeCy(container, elements, layout, style) {
    const base = {
      container: container, elements: elements, layout: layout, style: style,
      wheelSensitivity: 0.2, textureOnViewport: true, pixelRatio: 1,
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

  function layoutFor(nodeCount) {
    if (nodeCount <= 300) {
      // small graph: one-shot force layout (no ongoing physics)
      return { name: "cose", animate: false, fit: true, padding: 20,
               nodeRepulsion: 8000, idealEdgeLength: 60, numIter: 400 };
    }
    // large graph: deterministic O(n) grid; never auto-run force physics at scale (M5 adds on-demand)
    return { name: "grid", fit: true, padding: 20 };
  }

  function cyStyle(showLabels) {
    const s = [
      { selector: "node", style: {
        "background-color": KIND_COLORS.default,
        "width": "data(size)", "height": "data(size)",
        "label": showLabels ? "data(label)" : "",
        "font-size": 9, "color": "#c9d1d9",
        "text-valign": "center", "text-halign": "right", "text-margin-x": 4,
        "min-zoomed-font-size": 9,
      } },
      { selector: "edge", style: {
        "width": 1, "line-color": "#3a4753", "opacity": 0.5,
        "curve-style": "straight", "target-arrow-shape": "none",  // WebGL supports straight, not bezier
      } },
      { selector: "node:selected", style: {
        "border-width": 2, "border-color": "#ffd24d", "border-opacity": 1,
      } },
    ];
    Object.keys(KIND_COLORS).forEach((k) => {
      if (k === "default") return;
      s.push({ selector: 'node[kind="' + k + '"]', style: { "background-color": KIND_COLORS[k] } });
    });
    return s;
  }

  function destroyCy() {
    if (graphState.cy) {
      try { graphState.cy.destroy(); } catch (e) { /* already torn down */ }
      graphState.cy = null;
    }
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
    l.appendChild(swatchLabel(KIND_COLORS.function, "function / method"));
    l.appendChild(swatchLabel(KIND_COLORS.class, "class / type"));
    l.appendChild(swatchLabel(KIND_COLORS.component, "component / route"));
    l.appendChild(swatchLabel(KIND_COLORS.namespace, "namespace / module"));
    l.appendChild(swatchLabel(KIND_COLORS.file, "file (collapsed)"));
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
    if (!items.length) { box.appendChild(emptyMsg("No score history yet.")); return; }
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
  const TABS = Array.from(document.querySelectorAll(".tab[data-tab]")).map((a) => a.dataset.tab);
  const RENDER = {
    overview: renderOverview, history: renderHistory, calibration: renderCalibration,
    learning: renderLearning, graph: renderGraph,
  };
  function currentTab() {
    const h = location.hash.replace("#", "");
    return TABS.indexOf(h) >= 0 ? h : "overview";
  }
  let routing = false;
  async function route() {
    if (routing) return;
    routing = true;
    const tab = currentTab();
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.getAttribute("data-tab") === tab);
    });
    TABS.forEach((t) => { document.getElementById("view-" + t).hidden = t !== tab; });
    // Release the WebGL graph instance whenever the Graph tab is not the active one. The graphSeq
    // guard can't catch a switch-away-during-fetch (route() serialises renders, so no competing
    // loadFullGraph bumps the seq), and nothing else tears the instance down off-tab; this does.
    if (tab !== "graph") destroyCy();
    const view = document.getElementById("view-" + tab);
    view.removeAttribute("data-loaded");
    try {
      if (!repo) { view.hidden = false; clear(view); view.appendChild(emptyMsg("No repo selected (append &repo=<id> to the URL).")); }
      else await RENDER[tab](view);
      view.setAttribute("data-loaded", "true");
    } catch (e) {
      clear(view); view.appendChild(emptyMsg("Error loading " + tab + ": " + e.message));
    } finally {
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
