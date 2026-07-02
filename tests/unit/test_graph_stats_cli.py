"""`pebra graph-stats` CLI: registered, prints counts as JSON, returns 0; honest zeros with no graph."""

from __future__ import annotations

import json

from pebra import composition
from pebra.cli import graph_stats
from pebra.cli.main import build_parser


def test_graph_stats_is_registered():
    args = build_parser().parse_args(["graph-stats", "--repo-root", ".", "--json"])
    assert args.func is graph_stats.run_graph_stats


def test_graph_stats_json_reports_counts(capsys, monkeypatch):
    monkeypatch.setattr(composition, "graph_node_counts",
                        lambda repo_root: {"total": 900, "callable": 700, "csharp_callable": 680})
    args = build_parser().parse_args(["graph-stats", "--repo-root", "/x", "--json"])
    rc = graph_stats.run_graph_stats(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["csharp_callable"] == 680 and payload["command"] == "graph-stats"


def test_graph_stats_zero_when_no_graph(tmp_path, capsys):
    # real path against a dir with no CodeGraph index -> adapter returns honest zeros, rc 0
    args = build_parser().parse_args(["graph-stats", "--repo-root", str(tmp_path), "--json"])
    rc = graph_stats.run_graph_stats(args)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["csharp_callable"] == 0
