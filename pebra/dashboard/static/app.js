// PEBRA Risk Observatory — thin MVP (Phase 3b). Reads the bearer token + repo from the URL and
// renders the audit chain, an overview, and a recent-assessments table with current status.
// Vanilla JS, no framework; richer per-assessment detail panels arrive in full Phase 5c.
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  const token = params.get("token") || "";
  const repo = params.get("repo") || "";
  const app = document.getElementById("app");

  async function getJSON(path) {
    const res = await fetch(path, { headers: { Authorization: "Bearer " + token } });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  function el(tag, attrs, text) {
    const node = document.createElement(tag);
    if (attrs) { for (const k in attrs) node.setAttribute(k, attrs[k]); }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function pairs(obj) {
    return Object.keys(obj).map(function (k) { return k + ": " + obj[k]; }).join(", ");
  }

  const chainLabels = {};
  chainLabels["assessments"] = "Assessments run";
  chainLabels["outcomes"] = "Completed outcomes";
  chainLabels["prediction_" + "errors"] = "Predictions checked";
  chainLabels["risk_" + "snapshots"] = "Learning snapshots";
  chainLabels["learned_" + "risk_" + "facts"] = "Learned rules";

  function labeledPairs(obj, labels) {
    return Object.keys(obj).map(function (k) {
      return (labels[k] || k) + ": " + obj[k];
    }).join(", ");
  }

  async function render() {
    app.textContent = "";

    const chain = await getJSON("/api/chain-status");
    const chainCard = el("section", { "data-testid": "chain-status" });
    chainCard.appendChild(el("h2", null, "Audit chain"));
    chainCard.appendChild(
      el("p", null, (chain.valid ? "valid" : "BROKEN") + " — " + labeledPairs(chain.counts, chainLabels))
    );
    app.appendChild(chainCard);

    if (!repo) {
      app.appendChild(el("p", null, "No repo selected (append &repo=<id> to the URL)."));
      return;
    }

    const overview = await getJSON("/api/repos/" + encodeURIComponent(repo) + "/overview");
    const ov = el("section", { "data-testid": "overview" });
    ov.appendChild(el("h2", null, "Overview"));
    ov.appendChild(el("p", null, "Assessments run: " + overview.total));
    ov.appendChild(el("p", null, "By decision — " + pairs(overview.by_decision)));
    ov.appendChild(el("p", null, "By status — " + pairs(overview.by_status)));
    app.appendChild(ov);

    const data = await getJSON("/api/repos/" + encodeURIComponent(repo) + "/assessments?limit=50");
    const hist = el("section", { "data-testid": "history" });
    hist.appendChild(el("h2", null, "Recent assessments"));
    const table = el("table");
    const head = el("tr");
    ["assessment", "decision", "status", "confidence"].forEach(function (c) {
      head.appendChild(el("th", null, c));
    });
    table.appendChild(head);
    data.items.forEach(function (it) {
      const row = el("tr");
      const conf = it.scores && it.scores.edit_confidence;
      row.appendChild(el("td", null, it.assessment_id));
      row.appendChild(el("td", null, it.decision));
      row.appendChild(el("td", null, it.terminal_status || "pending"));
      row.appendChild(el("td", null, conf == null ? "" : String(conf)));
      table.appendChild(row);
    });
    hist.appendChild(table);
    app.appendChild(hist);
  }

  render().catch(function (err) {
    app.textContent = "Error loading dashboard: " + err.message;
  });
})();
