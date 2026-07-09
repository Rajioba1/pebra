"use strict";
// Render-only client for the run observatory. All server-sourced strings go through textContent /
// DOM APIs (never innerHTML), so a task_id authored on disk can never inject markup. Polls every 5s.

const POLL_MS = 5000;
const MIN_PAIRS_FOR_VERDICT = 3; // below this an assay verdict is structural noise, not a finding
let timer = null;

function el(tag, props = {}, children = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "href") n.setAttribute("href", v);
    else if (k === "title") n.title = v;
    else if (k === "value") n.value = v;
    else if (k === "readonly") n.readOnly = !!v;
    else n.setAttribute(k, v);
  }
  for (const c of [].concat(children)) if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  return n;
}
function kv(k, v) { return el("div", { class: "kv" }, [el("span", { class: "k", text: k }), el("span", { class: "v", text: v == null ? "—" : String(v) })]); }
function setPoll(state) { const p = document.getElementById("poll"); p.className = "poll " + state; p.textContent = state === "err" ? "poll error — retrying" : "live · every 5s"; }

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

// ---- run index ----
async function renderIndex() {
  const app = document.getElementById("app");
  let data;
  try { data = await getJSON("/api/runs"); setPoll("live"); } catch (e) { setPoll("err"); return; }
  app.replaceChildren();
  app.appendChild(el("h1", { text: "Runs" }));
  if (!data.runs.length) { app.appendChild(el("p", { class: "dim", text: "No runs under e2e/out/ab/ yet. Start one, then refresh." })); return; }
  const list = el("div", { class: "run-list" });
  for (const r of data.runs) {
    list.appendChild(el("a", { class: "run-row", href: "#/run/" + encodeURIComponent(r.run_id) }, [
      el("span", { class: "rid", text: r.run_id }),
      el("span", { class: "pill " + r.phase, text: r.phase }),
      el("span", { class: "spacer" }),
      el("span", { class: "dim", text: r.done_count + " arm-runs" }),
      el("span", { class: "dim mono", text: r.last_activity_iso ? r.last_activity_iso.replace("T", " ").slice(0, 19) : "" }),
    ]));
  }
  app.appendChild(list);
}

// ---- one run ----
function renderHeader(v) {
  const c = v.counts;
  const head = el("div", { class: "panel" }, [
    el("div", { class: "rhead" }, [
      el("div", {}, [el("h1", { text: v.run_id }), el("span", { class: "pill " + v.phase, text: v.phase })]),
      el("div", { class: "meta" }, [
        kv("mode", v.mode || "unknown"),
        kv("done", c.done),
        kv("pending", c.pending == null ? "—" : c.pending),
        kv("planned", c.total_planned == null ? "—" : c.total_planned),
        kv("last activity", v.phase_detail.last_activity_iso ? v.phase_detail.last_activity_iso.replace("T", " ").slice(0, 19) : "—"),
      ]),
    ]),
  ]);
  if (v.mode == null) head.appendChild(el("div", { class: "banner", text: "No mode known (run_status.json absent and no ?mode= given) — the matrix shows observed arms only; pending is unknown." }));
  return head;
}

function findPebraVsSham(pairwise) {
  return (pairwise || []).find((p) => p.intervention === "pebra" && p.baseline === "sham");
}

function renderScoreboard(sb) {
  const wrap = el("div", {});
  wrap.appendChild(el("h2", { text: "Scoreboard" }));
  if (sb.verdict !== undefined) {
    // assay scoreboard
    const key = findPebraVsSham(sb.pairwise);
    const nPairs = key ? key.n_pairs_risky : 0;
    const weak = nPairs < MIN_PAIRS_FOR_VERDICT;
    const vrow = el("div", { class: "verdict" + (weak ? " weak" : "") }, [
      el("span", { class: "v", text: sb.verdict }),
      el("span", { class: "npairs", text: "pebra·vs·sham risky pairs: " + nPairs }),
    ]);
    const panel = el("div", { class: "panel" }, [vrow]);
    if (weak) panel.appendChild(el("div", { class: "banner", text: "Too few matched pairs (" + nPairs + " < " + MIN_PAIRS_FOR_VERDICT + ") — this verdict is not yet meaningful. It will firm up as the run fills in." }));
    if (sb.conclusion) panel.appendChild(el("div", { class: "verdict-note", text: sb.conclusion }));
    panel.appendChild(renderArmTable(sb.arms));
    panel.appendChild(renderPairwiseTable(sb.pairwise));
    wrap.appendChild(panel);
  } else if (sb.endpoints) {
    // legacy AB scoreboard
    const e = sb.endpoints, np = sb.n_pairs || {};
    const panel = el("div", { class: "panel" }, [
      el("div", { class: "verdict" }, [el("span", { class: "npairs", text: "risky pairs: " + (np.risky ?? 0) + " · safe pairs: " + (np.safe ?? 0) })]),
      el("div", { class: "verdict-note", text: sb.conclusion || "" }),
      abEndpointTable(e),
    ]);
    wrap.appendChild(panel);
  }
  return wrap;
}

function pct(x) { return x == null ? "—" : (x * 100).toFixed(1) + "%"; }
function num(x) { return x == null ? "—" : Number(x).toFixed(3); }

function renderArmTable(arms) {
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "arm" }), el("th", { text: "n" }), el("th", { text: "harm" }), el("th", { text: "over-caution" }), el("th", { text: "completion" }), el("th", { text: "adherence" }), el("th", { text: "errors" }), el("th", { text: "leaks" })]));
  for (const [arm, a] of Object.entries(arms || {})) {
    t.appendChild(el("tr", {}, [
      el("td", { text: arm }), el("td", { class: "num", text: a.n_runs }),
      el("td", { class: "num", text: pct(a.harm_rate) }), el("td", { class: "num", text: pct(a.over_caution_rate) }),
      el("td", { class: "num", text: pct(a.task_completion_rate) }), el("td", { class: "num", text: pct(a.adherence_rate) }),
      el("td", { class: "num", text: a.error_run_count }), el("td", { class: "num", text: a.blinding_leak_count }),
    ]));
  }
  return t;
}
function renderPairwiseTable(pairwise) {
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "intervention" }), el("th", { text: "baseline" }), el("th", { text: "harm avoided" }), el("th", { text: "over-caution Δ" }), el("th", { text: "net benefit" }), el("th", { text: "risky pairs" })]));
  for (const p of pairwise || []) {
    t.appendChild(el("tr", {}, [
      el("td", { text: p.intervention }), el("td", { text: p.baseline }),
      el("td", { class: "num", text: num(p.harm_avoided_rate) }), el("td", { class: "num", text: num(p.over_caution_delta) }),
      el("td", { class: "num", text: num(p.net_benefit) }), el("td", { class: "num", text: p.n_pairs_risky }),
    ]));
  }
  return t;
}
function abEndpointTable(e) {
  const rows = [["harm rate", pct(e.harm_rate.control), pct(e.harm_rate.treatment)], ["over-caution", pct(e.over_caution_rate.control), pct(e.over_caution_rate.treatment)], ["completion", pct(e.task_completion_rate.control), pct(e.task_completion_rate.treatment)]];
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "endpoint" }), el("th", { text: "control" }), el("th", { text: "treatment" })]));
  for (const r of rows) t.appendChild(el("tr", {}, [el("td", { text: r[0] }), el("td", { class: "num", text: r[1] }), el("td", { class: "num", text: r[2] })]));
  t.appendChild(el("tr", {}, [el("td", { text: "net benefit" }), el("td", { class: "num dim", text: "" }), el("td", { class: "num", text: num(e.net_benefit) })]));
  return t;
}

function renderMatrix(matrix) {
  const wrap = el("div", {});
  wrap.appendChild(el("h2", { text: "Task × Seed × Arm" }));
  const arms = [...new Set(matrix.map((m) => m.arm))].sort();
  const rows = [...new Set(matrix.map((m) => m.task_id + " · seed " + m.seed))].sort();
  const byKey = new Map(matrix.map((m) => [m.task_id + " · seed " + m.seed + "|" + m.arm, m]));
  const table = el("table", { class: "matrix" });
  table.appendChild(el("tr", {}, [el("th", { class: "rowh", text: "" }), ...arms.map((a) => el("th", { text: a }))]));
  for (const row of rows) {
    const tr = el("tr", {}, [el("th", { class: "rowh", text: row })]);
    for (const a of arms) {
      const m = byKey.get(row + "|" + a);
      const td = el("td", {});
      if (!m) { td.appendChild(el("span", { class: "cell pending", title: "not planned" })); }
      else if (m.status === "pending") { td.appendChild(el("span", { class: "cell pending", title: "pending" })); }
      else {
        const s = m.outcome_summary || {};
        let cls = "cell done", title = "done";
        if (s.harm_materialized) { cls = "cell harm"; title = "harm materialized"; }
        else if (s.over_cautious) { cls = "cell caution"; title = "over-cautious"; }
        else if (s.error) { title = "error: " + s.error; }
        td.appendChild(el("span", { class: cls, title }));
      }
      tr.appendChild(td);
    }
    table.appendChild(tr);
  }
  const legend = el("div", { class: "legend" }, [
    el("span", {}, [el("span", { class: "cell done" }), " done"]),
    el("span", {}, [el("span", { class: "cell harm" }), " harm"]),
    el("span", {}, [el("span", { class: "cell caution" }), " over-caution"]),
    el("span", {}, [el("span", { class: "cell pending" }), " pending"]),
  ]);
  wrap.appendChild(el("div", { class: "panel matrix-wrap" }, [table, legend]));
  return wrap;
}

function renderGroups(groups) {
  const wrap = el("div", {});
  wrap.appendChild(el("h2", { text: "Specimen coverage" }));
  const panel = el("div", { class: "panel groups" });
  for (const [title, rows] of [["language", groups && groups.by_language], ["specimen", groups && groups.by_specimen]]) {
    const table = el("table", { class: "data" });
    table.appendChild(el("tr", {}, [el("th", { text: title }), el("th", { text: "done" }), el("th", { text: "pending" }), el("th", { text: "planned" })]));
    for (const [name, counts] of Object.entries(rows || {})) {
      table.appendChild(el("tr", {}, [
        el("td", { text: name }), el("td", { class: "num", text: counts.done }),
        el("td", { class: "num", text: counts.pending }), el("td", { class: "num", text: counts.total_planned }),
      ]));
    }
    panel.appendChild(table);
  }
  wrap.appendChild(panel);
  return wrap;
}

function renderCoverage(cov) {
  if (!cov || !cov.available) return el("div", {}, [el("h2", { text: "Language coverage" }), el("p", { class: "dim", text: cov && cov.reason ? cov.reason : "not available" })]);
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "language" }), el("th", { text: "tier" }), el("th", { text: "nodes" })]));
  for (const [lang, c] of Object.entries(cov.by_language || {})) t.appendChild(el("tr", {}, [el("td", { text: lang }), el("td", { text: c.tier }), el("td", { class: "num", text: c.node_count })]));
  return el("div", {}, [el("h2", { text: "Language coverage" }), el("div", { class: "panel" }, [t])]);
}

function renderDashboards(dashboards) {
  const wrap = el("div", {});
  wrap.appendChild(el("h2", { text: "Open real PEBRA dashboard (per arm)" }));
  if (!dashboards.length) { wrap.appendChild(el("p", { class: "dim", text: "No PEBRA stores yet — only the pebra / pebra_graph_repair arms write one." })); return wrap; }
  const panel = el("div", { class: "panel" });
  for (const d of dashboards) {
    const armTag = el("span", { class: "tag arm", text: d.arm || "unattributed" });
    const cmd = el("input", { class: "mono", readonly: true, value: d.launch_command || "(no repo/ dir — cannot resolve --repo-root)" });
    const btn = el("button", { class: "btn", text: "copy" });
    btn.addEventListener("click", () => { cmd.select(); navigator.clipboard && navigator.clipboard.writeText(cmd.value); btn.textContent = "copied"; setTimeout(() => (btn.textContent = "copy"), 1200); });
    panel.appendChild(el("div", { class: "dash-cmd" }, [armTag, cmd, btn]));
  }
  panel.appendChild(el("p", { class: "dim", text: "Paste into a terminal — it launches the real product dashboard for that arm's store." }));
  wrap.appendChild(panel);
  return wrap;
}

async function renderRun(runId, mode) {
  const app = document.getElementById("app");
  let v;
  const query = mode ? "?mode=" + encodeURIComponent(mode) : "";
  try { v = await getJSON("/api/run/" + encodeURIComponent(runId) + query); setPoll("live"); }
  catch (e) { setPoll("err"); if (String(e.message) === "404") { app.replaceChildren(el("p", { class: "dim", text: "Run '" + runId + "' not found." }), el("p", {}, [el("a", { href: "#/", text: "← all runs" })])); } return; }
  app.replaceChildren(
    el("p", {}, [el("a", { href: "#/", text: "← all runs" })]),
    renderHeader(v),
    renderScoreboard(v.scoreboard),
    renderGroups(v.groups),
    renderMatrix(v.matrix),
    renderCoverage(v.coverage),
    renderDashboards(v.dashboards),
  );
}

// ---- router ----
function route() {
  if (timer) { clearInterval(timer); timer = null; }
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/run\/([^?]+)(?:\?(.+))?$/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    const params = new URLSearchParams(m[2] || "");
    const mode = params.get("mode");
    renderRun(id, mode);
    timer = setInterval(() => renderRun(id, mode), POLL_MS);
  }
  else { renderIndex(); timer = setInterval(renderIndex, POLL_MS); }
}
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
