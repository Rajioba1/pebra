"""External-repo e2e lane (heavy, gated E2E_EXTERNAL=1).

Proves what the synthetic lane cannot: real CodeGraph indexing of a real C# repo, graph-derived fan-in
(the graph-vs-no-graph delta), a real `dotnet build` as the outcome signal, and (later) an agent A/B.
The external source repo is NEVER mutated — it is cloned at its HEAD into the gitignored e2e/out/repos/.
"""
