"use strict";
// Render-only client for the run observatory. All server-sourced strings go through textContent /
// DOM APIs (never innerHTML), so a task_id authored on disk can never inject markup. Polls every 5s.

const POLL_MS = 5000;
let timer = null;
const launchState = new Map(); // key: run_id|clone -> {state, text}

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

function captureViewState(app) {
  const active = document.activeElement;
  return {
    scrollX: window.scrollX,
    scrollY: window.scrollY,
    openDetailKeys: new Set(
      [...app.querySelectorAll("details[data-state-key]")]
        .filter((details) => details.open)
        .map((details) => details.dataset.stateKey),
    ),
    tableScrollOffsets: new Map(
      [...app.querySelectorAll(".table-scroll")]
        .map((node, index) => [tableScrollKey(node, index), { x: node.scrollLeft, y: node.scrollTop }]),
    ),
    focusedControlKey: active && app.contains(active) ? active.dataset.focusKey || null : null,
  };
}

function tableScrollKey(node, index) {
  const headings = [...node.querySelectorAll("th")].map((heading) => heading.textContent).join("|");
  return headings || String(index);
}

function restoreViewState(app, state) {
  app.querySelectorAll("details[data-state-key]").forEach((details) => {
    details.open = state.openDetailKeys.has(details.dataset.stateKey);
  });
  app.querySelectorAll(".table-scroll").forEach((node, index) => {
    const offset = state.tableScrollOffsets.get(tableScrollKey(node, index));
    if (offset) { node.scrollLeft = offset.x; node.scrollTop = offset.y; }
  });
  if (state.focusedControlKey) {
    const control = [...app.querySelectorAll("[data-focus-key]")]
      .find((node) => node.dataset.focusKey === state.focusedControlKey);
    if (control) control.focus({ preventScroll: true });
  }
  window.scrollTo(state.scrollX, state.scrollY);
}

function replaceApp(app, children) {
  const state = captureViewState(app);
  app.replaceChildren(...children);
  restoreViewState(app, state);
}

// ---- run index ----
async function renderIndex() {
  const app = document.getElementById("app");
  let data;
  try { data = await getJSON("/api/runs"); setPoll("live"); } catch (e) { setPoll("err"); return; }
  const children = [el("h1", { text: "Runs" })];
  if (!data.runs.length) {
    children.push(el("p", { class: "dim", text: "No runs under e2e/out/ab/ yet. Start one, then refresh." }));
    replaceApp(app, children);
    return;
  }
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
  children.push(list);
  replaceApp(app, children);
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
  if (v.phase_detail.error) {
    const kind = v.phase_detail.failure_kind ? v.phase_detail.failure_kind + ": " : "";
    head.appendChild(el("div", { class: "banner failure", text: kind + v.phase_detail.error }));
  }
  return head;
}

function findPebraVsSham(pairwise) {
  return (pairwise || []).find((p) => p.intervention === "pebra" && p.baseline === "sham");
}

function tableScroll(table) { return el("div", { class: "table-scroll" }, [table]); }
function detailSection(label, child, open = false) {
  const summary = el("summary", { text: label });
  summary.dataset.focusKey = "details:" + label;
  const details = el("details", { class: "metric-detail" }, [summary, child]);
  details.dataset.stateKey = label;
  if (open) details.open = true;
  return details;
}

function renderScoreboard(sb) {
  const wrap = el("div", {});
  wrap.appendChild(el("h2", { text: "Scoreboard" }));
  if (sb.verdict !== undefined) {
    // assay scoreboard
    const key = findPebraVsSham(sb.pairwise);
    const nPairs = key ? key.n_pairs_risky : 0;
    const diagnostic = !!sb.diagnostic_only;
    const vrow = el("div", { class: "verdict" + (diagnostic ? " weak" : "") }, [
      el("span", { class: "v", text: diagnostic ? "Live diagnostic" : "Claim result" }),
      el("span", { class: "tag", text: sb.verdict }),
      el("span", { class: "npairs", text: "pebra·vs·sham risky pairs: " + nPairs }),
    ]);
    const panel = el("div", { class: "panel" }, [vrow]);
    if (diagnostic) panel.appendChild(el("div", { class: "banner", text: "Diagnostic evidence only. Read the observed counts and mechanism trace; no efficacy claim is authorized for this run design." }));
    if (sb.conclusion) panel.appendChild(el("div", { class: "verdict-note", text: sb.conclusion }));
    panel.appendChild(renderArmTable(sb.arms));
    panel.appendChild(renderPairwiseTable(sb.pairwise));
    panel.appendChild(detailSection("Mechanism diagnostics", renderMechanismTable(sb.arms, sb.pairwise)));
    if (Object.values(sb.arms || {}).some((a) => (a.approval_offered_count || 0) > 0)) {
      panel.appendChild(detailSection("Simulated approval-path diagnostics", renderGovernanceTable(sb.arms)));
    }
    panel.appendChild(detailSection("Integrity and exclusions", renderIntegrityTable(sb.arms)));
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
  t.appendChild(el("tr", {}, [el("th", { text: "arm" }), el("th", { class: "num", text: "n" }), el("th", { class: "num", text: "harm" }), el("th", { class: "num", text: "completion" }), el("th", { class: "num", text: "verified autonomous" }), el("th", { class: "num", text: "escalation" }), el("th", { class: "num", text: "errors" })]));
  for (const [arm, a] of Object.entries(arms || {})) {
    t.appendChild(el("tr", {}, [
      el("td", { text: arm }), el("td", { class: "num", text: a.n_runs }),
      el("td", { class: "num", text: pct(a.harm_rate) }),
      el("td", { class: "num", text: pct(a.task_completion_rate) }),
      el("td", { class: "num", text: pct(a.graph_refined_autonomous_completion_rate) }),
      el("td", { class: "num", text: pct(a.safe_escalation_rate) }),
      el("td", { class: "num", text: a.error_run_count }),
    ]));
  }
  return tableScroll(t);
}
function renderPairwiseTable(pairwise) {
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "intervention" }), el("th", { text: "baseline" }), el("th", { class: "num", text: "harm avoided" }), el("th", { class: "num", text: "completion Δ" }), el("th", { class: "num", text: "over-caution Δ" }), el("th", { class: "num", text: "harm-over-caution balance", title: "Unweighted harm avoided minus over-caution; not total or decision-curve benefit." }), el("th", { class: "num", text: "risky pairs" }), el("th", { class: "num", text: "safe pairs" }), el("th", { class: "num", text: "independent tasks" }), el("th", { text: "harm 95% CI" })]));
  for (const p of pairwise || []) {
    const ci = p.harm_diff_ci95 ? "[" + num(p.harm_diff_ci95[0]) + ", " + num(p.harm_diff_ci95[1]) + "]" : "—";
    t.appendChild(el("tr", {}, [
      el("td", { text: p.intervention }), el("td", { text: p.baseline }),
      el("td", { class: "num", text: (p.harm_avoided_count ?? 0) + "/" + p.n_pairs_risky + " (" + num(p.harm_avoided_rate) + ")" }),
      el("td", { class: "num", text: (p.completion_gain_count ?? 0) + "/" + p.n_pairs_risky + " (" + num(p.risky_completion_gain) + ")" }),
      el("td", { class: "num", text: num(p.over_caution_delta) }),
      el("td", { class: "num", text: num(p.harm_overcaution_balance ?? p.net_benefit) }),
      el("td", { class: "num", text: p.n_pairs_risky }),
      el("td", { class: "num", text: p.n_pairs_safe }),
      el("td", { class: "num", text: p.n_independent_risky_tasks ?? 0 }),
      el("td", { text: ci }),
    ]));
  }
  return tableScroll(t);
}

function renderMechanismTable(arms, pairwise) {
  const wrap = el("div");
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "arm" }), el("th", { class: "num", text: "decision cycle" }), el("th", { class: "num", text: "adherence" }), el("th", { class: "num", text: "graph-only verified" }), el("th", { class: "num", text: "graph + host verified" })]));
  for (const [arm, a] of Object.entries(arms || {})) t.appendChild(el("tr", {}, [el("td", { text: arm }), el("td", { class: "num", text: pct(a.decision_cycle_completion_rate) }), el("td", { class: "num", text: pct(a.adherence_rate) }), el("td", { class: "num", text: pct(a.graph_only_autonomous_completion_rate) }), el("td", { class: "num", text: pct(a.graph_plus_host_verified_completion_rate) })]));
  wrap.appendChild(tableScroll(t));
  const gains = el("table", { class: "data" });
  gains.appendChild(el("tr", {}, [el("th", { text: "intervention" }), el("th", { text: "baseline" }), el("th", { class: "num", text: "autonomous Δ" }), el("th", { class: "num", text: "graph-only verified Δ" }), el("th", { class: "num", text: "graph + host verified Δ" })]));
  for (const p of pairwise || []) gains.appendChild(el("tr", {}, [el("td", { text: p.intervention }), el("td", { text: p.baseline }), el("td", { class: "num", text: num(p.autonomous_completion_gain) }), el("td", { class: "num", text: num(p.graph_only_autonomous_completion_gain) }), el("td", { class: "num", text: num(p.graph_plus_host_verified_completion_gain) })]));
  wrap.appendChild(tableScroll(gains));
  return wrap;
}

function renderGovernanceTable(arms) {
  const wrap = el("div", {}, [el("p", { class: "dim", text: "A deterministic approval policy tests plumbing, not human judgment." })]);
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "arm" }), el("th", { class: "num", text: "approval request" }), el("th", { class: "num", text: "deterministic policy grant" }), el("th", { class: "num", text: "reassessment" }), el("th", { class: "num", text: "simulated approval-path" })]));
  for (const [arm, a] of Object.entries(arms || {})) if ((a.approval_offered_count || 0) > 0) t.appendChild(el("tr", {}, [el("td", { text: arm }), el("td", { class: "num", text: pct(a.approval_request_adherence_rate) }), el("td", { class: "num", text: pct(a.deterministic_policy_grant_rate ?? a.approval_grant_rate) }), el("td", { class: "num", text: pct(a.post_approval_reassessment_rate) }), el("td", { class: "num", text: pct(a.simulated_approval_path_completion_rate ?? a.human_assisted_completion_rate) })]));
  wrap.appendChild(tableScroll(t));
  return wrap;
}

function renderIntegrityTable(arms) {
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "arm" }), el("th", { class: "num", text: "no attempt" }), el("th", { class: "num", text: "errors" }), el("th", { class: "num", text: "blinding leaks" }), el("th", { class: "num", text: "write before approval" }), el("th", { class: "num", text: "write before reassess" })]));
  for (const [arm, a] of Object.entries(arms || {})) t.appendChild(el("tr", {}, [el("td", { text: arm }), el("td", { class: "num", text: a.no_attempt_count || 0 }), el("td", { class: "num", text: a.error_run_count || 0 }), el("td", { class: "num", text: a.blinding_leak_count || 0 }), el("td", { class: "num", text: pct(a.write_before_approval_rate) }), el("td", { class: "num", text: pct(a.write_before_reassessment_rate) })]));
  return tableScroll(t);
}
function abEndpointTable(e) {
  const rows = [["harm rate", pct(e.harm_rate.control), pct(e.harm_rate.treatment)], ["over-caution", pct(e.over_caution_rate.control), pct(e.over_caution_rate.treatment)], ["completion", pct(e.task_completion_rate.control), pct(e.task_completion_rate.treatment)]];
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "endpoint" }), el("th", { class: "num", text: "control" }), el("th", { class: "num", text: "treatment" })]));
  for (const r of rows) t.appendChild(el("tr", {}, [el("td", { text: r[0] }), el("td", { class: "num", text: r[1] }), el("td", { class: "num", text: r[2] })]));
  t.appendChild(el("tr", {}, [el("td", { text: "harm-over-caution balance" }), el("td", { class: "num dim", text: "" }), el("td", { class: "num", text: num(e.harm_overcaution_balance ?? e.net_benefit) })]));
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
      if (!m) { td.appendChild(el("span", { class: "cell na", title: "not planned" })); }
      else if (m.status === "pending") { td.appendChild(el("span", { class: "cell pending", title: "pending" })); }
      else {
        const s = m.outcome_summary || {};
        let cls = "cell done", title = "done";
        if (s.no_attempt) { cls = "cell noattempt"; title = "no attempt" + (s.limit_reason ? ": " + s.limit_reason : ""); }
        else if (s.harm_materialized) { cls = "cell harm"; title = "harm materialized"; }
        else if (s.over_cautious) { cls = "cell caution"; title = "over-cautious"; }
        else if (s.error) { title = "error: " + s.error; }
        if (s.completion_test_ran) title += " · completion check " + (s.completion_test_passed ? "passed" : "failed");
        if (s.decision_cycle_completed) title += " · governance " + s.terminal_governance_outcome;
        if (s.human_approval_offered) title += " · approval offered";
        if (s.graph_refinement_proof_path) {
          title += " · pre-edit route observed: " + s.graph_refinement_proof_path;
        }
        if (s.graph_refinement_assessment_id) {
          title += " · graph assessment " + s.graph_refinement_assessment_id;
        }
        if (s.applied_assessment_id) title += " · applied assessment " + s.applied_assessment_id;
        if (s.post_edit_verify_ran) {
          title += " · post-edit verify " + (s.post_edit_verify_passed ? "passed" : "failed");
          if (s.post_edit_verify_assessment_id) {
            title += " (" + s.post_edit_verify_assessment_id + ")";
          }
        }
        if (s.graph_refined_completion_credited) {
          title += " · credited graph-refined completion";
        }
        if (s.candidate_lineage_invalidated) title += " · candidate lineage invalidated";
        if (s.graph_refinement_revision_risk_benefit_improved) {
          title += " · loss " + num(s.graph_refinement_origin_expected_loss) + "→" + num(s.graph_refinement_revised_expected_loss);
          title += " · benefit " + num(s.graph_refinement_origin_benefit) + "→" + num(s.graph_refinement_revised_benefit);
          title += " · EU " + num(s.graph_refinement_origin_expected_utility) + "→" + num(s.graph_refinement_revised_expected_utility);
          title += " · RAU " + num(s.graph_refinement_origin_rau) + "→" + num(s.graph_refinement_revised_rau);
          if (s.measured_benefit_deltas && Object.keys(s.measured_benefit_deltas).length) {
            title += " · measured benefit " + num(s.measured_benefit);
          }
        }
        if (s.human_approval_requested) title += " · approval requested";
        if (s.human_approval_granted) title += " · approval granted";
        if (s.post_approval_reassessment) title += " · exact candidate reassessed";
        if (s.write_before_approval) title += " · wrote before approval";
        if (s.write_before_reassessment) title += " · wrote before reassessment";
        title += s.protocol_file_read ? " · protocol read" : " · protocol not read";
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
    el("span", {}, [el("span", { class: "cell noattempt" }), " no attempt"]),
    el("span", {}, [el("span", { class: "cell pending" }), " pending"]),
    el("span", {}, [el("span", { class: "cell na" }), " not planned"]),
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
    table.appendChild(el("tr", {}, [el("th", { text: title }), el("th", { class: "num", text: "done" }), el("th", { class: "num", text: "pending" }), el("th", { class: "num", text: "planned" })]));
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

function renderTraces(traces) {
  const wrap = el("div", {});
  wrap.appendChild(el("h2", { text: "Subject traces" }));
  if (!traces || !traces.length) {
    wrap.appendChild(el("p", { class: "dim", text: "No subject_trace.json sidecars yet. New runs write one per arm clone." }));
    return wrap;
  }
  const rows = traces.slice().sort((a, b) => (
    String(a.task_id || "").localeCompare(String(b.task_id || ""))
    || Number(a.seed || 0) - Number(b.seed || 0)
    || String(a.arm || "").localeCompare(String(b.arm || ""))
  ));
  const table = el("table", { class: "data trace-table" });
  table.appendChild(el("tr", {}, [
    el("th", { text: "task" }),
    el("th", { text: "arm" }),
    el("th", { class: "num", text: "turns" }),
    el("th", { class: "num", text: "tools" }),
    el("th", { text: "timeout" }),
    el("th", { text: "protocol" }),
    el("th", { text: "advisory" }),
    el("th", { text: "writes" }),
    el("th", { text: "last" }),
    el("th", { class: "num", text: "duration" }),
  ]));
  for (const t of rows) {
    const decisions = (t.advisory_decisions || []).join(", ") || "—";
    const timeoutText = t.timed_out ? (t.limit_reason || "timeout") : "no";
    const writeText = String(t.write_count || 0) + (t.blocked_write_count ? " · blocked " + t.blocked_write_count : "");
    const lastText = [t.last_tool_name || "—", t.last_turn_stop_reason || ""].filter(Boolean).join(" · ");
    const rowClass = t.timed_out ? "trace-timeout" : "";
    table.appendChild(el("tr", { class: rowClass }, [
      el("td", { text: String(t.task_id || "—") + " · seed " + String(t.seed ?? "—") }),
      el("td", { text: t.arm || "—" }),
      el("td", { class: "num", text: t.turn_count ?? "—" }),
      el("td", { class: "num", text: t.tool_call_count ?? "—" }),
      el("td", { text: timeoutText }),
      el("td", { text: t.protocol_file_read ? "read" : "not read" }),
      el("td", { text: String(t.advisory_count || 0) + " · " + decisions }),
      el("td", { text: writeText }),
      el("td", { text: lastText }),
      el("td", { class: "num", text: t.duration_seconds == null ? "—" : Number(t.duration_seconds).toFixed(1) + "s" }),
    ]));
  }
  wrap.appendChild(el("div", { class: "panel trace-wrap" }, [
    table,
    el("p", { class: "dim", text: "Trace rows summarize subject_trace.json sidecars: model turns, tool calls, protocol-file reads, advisory decisions, write blocks, and timeout reason." }),
  ]));
  return wrap;
}

function renderCoverage(cov) {
  if (!cov || !cov.available) return el("div", {}, [el("h2", { text: "Language coverage" }), el("p", { class: "dim", text: cov && cov.reason ? cov.reason : "not available" })]);
  const t = el("table", { class: "data" });
  t.appendChild(el("tr", {}, [el("th", { text: "language" }), el("th", { text: "tier" }), el("th", { class: "num", text: "nodes" })]));
  for (const [lang, c] of Object.entries(cov.by_language || {})) t.appendChild(el("tr", {}, [el("td", { text: lang }), el("td", { text: c.tier }), el("td", { class: "num", text: c.node_count })]));
  return el("div", {}, [el("h2", { text: "Language coverage" }), el("div", { class: "panel" }, [t])]);
}

function launchKey(runId, clone) { return runId + "|" + clone; }
function setLaunchState(key, state, text) {
  launchState.set(key, { ...(launchState.get(key) || {}), state, text });
}
function setCopyText(key, copyText) {
  launchState.set(key, { ...(launchState.get(key) || {}), copyText });
  document.querySelectorAll("button[data-launch-key]").forEach((button) => {
    if (button.dataset.launchKey === key) button.textContent = copyText;
  });
}
function applyLaunchState(key, openBtn, copyBtn, status) {
  const current = launchState.get(key);
  openBtn.disabled = current && current.state === "launching";
  copyBtn.textContent = current && current.copyText ? current.copyText : "copy";
  status.textContent = current ? current.text : "";
}
function applyCurrentLaunchState(key) {
  document.querySelectorAll(".dash-cmd").forEach((row) => {
    if (row.dataset.launchKey !== key) return;
    const openBtn = row.querySelector('[data-launch-role="open"]');
    const copyBtn = row.querySelector('[data-launch-role="copy"]');
    const status = row.querySelector(".launch-status");
    if (openBtn && copyBtn && status) applyLaunchState(key, openBtn, copyBtn, status);
  });
}

function renderDashboards(runId, dashboards) {
  const wrap = el("div", {});
  wrap.appendChild(el("h2", { text: "Open real PEBRA dashboard (per arm)" }));
  if (!dashboards.length) { wrap.appendChild(el("p", { class: "dim", text: "No PEBRA stores yet — only the pebra / pebra_graph_repair / treatment arms write one." })); return wrap; }
  const panel = el("div", { class: "panel" });
  for (const d of dashboards) {
    const armTag = el("span", { class: "tag arm", text: d.arm || "unattributed" });
    const cmd = el("input", { class: "mono", readonly: true, value: d.launch_command || "(no repo/ dir — cannot derive --repo-id)" });
    const openBtn = el("button", { class: "btn", text: "Open" });
    const copyBtn = el("button", { class: "btn ghost", text: "copy" });
    const status = el("span", { class: "dim launch-status" });
    const key = launchKey(runId, d.clone);
    openBtn.dataset.focusKey = "open:" + key;
    openBtn.dataset.launchRole = "open";
    copyBtn.dataset.focusKey = "copy:" + key;
    copyBtn.dataset.launchKey = key;
    copyBtn.dataset.launchRole = "copy";
    cmd.dataset.focusKey = "command:" + key;
    applyLaunchState(key, openBtn, copyBtn, status);
    openBtn.addEventListener("click", async () => {
      if (!d.repo) { status.textContent = "no repo/ dir"; return; }
      if (launchState.get(key)?.state === "launching") return;
      const tab = window.open("about:blank", "_blank", "noopener");
      setLaunchState(key, "launching", "launching…");
      applyLaunchState(key, openBtn, copyBtn, status);
      try {
        const r = await fetch("/api/launch", { method: "POST", headers: { "Content-Type": "application/json", "X-PEBRA-Observatory": "1" }, body: JSON.stringify({ run_id: runId, clone: d.clone }) });
        const j = await r.json();
        if (r.ok && j.url) {
          if (tab) tab.location = j.url;
          setLaunchState(key, "opened", tab ? (j.status === "already_running" ? "already open ↗" : "opened ↗") : "popup blocked — use copy");
        } else {
          if (tab) tab.close();
          setLaunchState(key, "failed", "failed: " + (j.reason || r.status));
        }
      } catch (e) {
        if (tab) tab.close();
        setLaunchState(key, "failed", "launch error — use copy");
      }
      applyLaunchState(key, openBtn, copyBtn, status);
      applyCurrentLaunchState(key);
    });
    copyBtn.addEventListener("click", async () => {
      cmd.select();
      try {
        if (!navigator.clipboard) throw new Error("clipboard unavailable");
        await navigator.clipboard.writeText(cmd.value);
        setCopyText(key, "copied");
      } catch (e) {
        setCopyText(key, "copy failed");
      }
      setTimeout(() => setCopyText(key, "copy"), 1200);
    });
    const row = el("div", { class: "dash-cmd" }, [armTag, openBtn, copyBtn, cmd, status]);
    row.dataset.launchKey = key;
    panel.appendChild(row);
  }
  panel.appendChild(el("p", { class: "dim", text: "Open serves a validated temp copy on its own port. Copy gives a direct read-only fallback command for after-run inspection." }));
  wrap.appendChild(panel);
  return wrap;
}

async function renderRun(runId, mode) {
  const app = document.getElementById("app");
  let v;
  const query = mode ? "?mode=" + encodeURIComponent(mode) : "";
  try { v = await getJSON("/api/run/" + encodeURIComponent(runId) + query); setPoll("live"); }
  catch (e) {
    setPoll("err");
    if (String(e.message) === "404") {
      replaceApp(app, [el("p", { class: "dim", text: "Run '" + runId + "' not found." }), el("p", {}, [el("a", { href: "#/", text: "← all runs" })])]);
    }
    return;
  }
  replaceApp(app, [
    el("p", {}, [el("a", { href: "#/", text: "← all runs" })]),
    renderHeader(v),
    renderScoreboard(v.scoreboard),
    renderGroups(v.groups),
    renderMatrix(v.matrix),
    renderTraces(v.traces),
    renderCoverage(v.coverage),
    renderDashboards(v.run_id, v.dashboards),
  ]);
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
