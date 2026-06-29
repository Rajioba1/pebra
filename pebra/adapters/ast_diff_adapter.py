"""ast_diff_adapter (Phase-0 SymbolDiffProvider, AD-27) — owns symbol-diff I/O.

Phase 0: seeded with the request's ``symbol_diff`` evidence slice (a plain dict), it returns that
directly; otherwise it falls back conservatively to a cold-start ``UNKNOWN`` summary (file/path-level
risk). It deliberately does NOT hold the whole AssessmentRequest — only its own evidence slice, like
any adapter config. Real AST parsing of ``action.proposed_patch`` + per-symbol fan-in lookup arrives
in Phase 2; until then ``fallback_reason`` records why we are not at symbol granularity.
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import replace
from typing import Any

from pebra.adapters.patch_header_adapter import parse_patch_headers
from pebra.core.models import CandidateAction, SymbolDiffEvidence

_ALLOWED = set(SymbolDiffEvidence.__dataclass_fields__)

# File-op dominance when a patch touches several files: DELETE (symbol loss) > MOVE/RENAME (path
# migration) > CREATE (no callers). Drives the single file_operation_kind axis.
_FILE_OP_SEVERITY = {"CREATE": 1, "RENAME": 2, "MOVE": 2, "DELETE": 3}

# Body-only source similarity below this ratio (signature unchanged, body wholly rewritten) flags a
# suspected identity replacement. Empirically calibrated: a total rewrite scores ~0.45 body-only while
# a legitimate variable-rename refactor scores ~0.68, so 0.5 separates them. Conservative by design —
# it may flag very aggressive refactors (extra CONTRACT review), never the reverse. Compares BODY only
# because the shared `def`/signature line inflates full-source similarity above any useful threshold.
# Known limitation: for very short bodies (~≤3 lines) shared tokens (return, arg names) keep the ratio
# above 0.5 even for a total semantic swap, so identity-replacement detection is only effective for
# functions with 4+ body lines. That is the accepted conservative (false-negative) direction.
_IDENTITY_REPLACEMENT_THRESHOLD: float = 0.5


def _functions(source: str) -> dict[str, ast.AST]:
    """Map qualified function/method name -> def node. Raises SyntaxError on unparseable source."""
    tree = ast.parse(source)
    funcs: dict[str, ast.AST] = {}

    class _V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def _add(self, node: ast.AST, name: str) -> None:
            qual = ".".join([*self.stack, name])
            funcs[qual] = node

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._add(node, node.name)
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    _V().visit(tree)
    return funcs


_CONTROL_FLOW = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.AsyncFor, ast.AsyncWith)


def _control_flow_count(node: ast.AST) -> int:
    return sum(isinstance(n, _CONTROL_FLOW) for n in ast.walk(node))


def _visibility(qual: str) -> str:
    leaf = qual.split(".")[-1]
    return "private" if leaf.startswith("_") else "internal"


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Drop a leading docstring statement so it doesn't count as a semantic body change (AD-27:
    ordinary docstrings are cosmetic)."""
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _normalized_module_dump(src: str) -> str:
    """ast.dump of the whole module with docstrings stripped (module + every def/class). Used to
    detect module/class-level semantic changes (constants, imports, class bases, decorators) that the
    per-function diff doesn't capture, while ignoring docstring/comment/whitespace-only edits."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.body = _strip_docstring(node.body)
    return ast.dump(tree)


def _has_semantic_module_content(src: str | None) -> bool:
    if src is None:
        return False
    tree = ast.parse(src)
    tree.body = _strip_docstring(tree.body)
    return bool(tree.body)


def parses(src: str | None) -> bool:
    """True if the source parses (or is absent). Lets the verifier tell 'cosmetic, no semantic
    change' (parses, no rows -> COSMETIC) from 'couldn't parse' (-> UNKNOWN)."""
    if src is None:
        return True
    try:
        ast.parse(src)
    except SyntaxError:
        return False
    return True


def _body_source(src: str | None, statements: list[ast.stmt]) -> str:
    """Concatenated source of a function's body statements (docstring already stripped by caller)."""
    if not src:
        return ""
    parts = [ast.get_source_segment(src, stmt) for stmt in statements]
    return "\n".join(p for p in parts if p)


def _row(symbol_id: str, qual: str, *, signature_changed: bool, body_changed: bool,
         control_flow_changed: bool, identity_replacement_suspected: bool = False) -> dict[str, Any]:
    return {
        "symbol_id": symbol_id,
        "visibility": _visibility(qual),
        "signature_changed": signature_changed,
        "return_shape_changed": False,
        "body_changed": body_changed,
        "control_flow_changed": control_flow_changed,
        "identity_replacement_suspected": identity_replacement_suspected,
        "external_side_effect_changed": False,
        "db_write_changed": False,
        "payment_api_changed": False,
        "migration_changed": False,
        "directive_comment_changed": False,
        "test_only": False,
        "callers_percentile": 0.0,
        "transitive_reaches_consequence_symbol": False,
    }


_COMPLEXITY_NODES = (
    ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler, ast.With, ast.AsyncFor,
    ast.AsyncWith, ast.BoolOp, ast.IfExp, ast.comprehension,
)


def _complexity(source: str | None) -> int:
    if not source:
        return 0
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return sum(isinstance(n, _COMPLEXITY_NODES) for n in ast.walk(tree))


def compute_complexity_delta(before_src: str | None, after_src: str | None) -> float:
    """Measured cyclomatic-style complexity change (after - before); negative = simpler = better.

    A stdlib measured signal for post-edit benefit deltas (Architecture §9 / spec §6). Unparseable
    source contributes 0 (we don't measure what we can't parse). radon-grade MI is a later upgrade.
    """
    return float(_complexity(after_src) - _complexity(before_src))


def compute_symbol_diff_rows(
    before_src: str | None, after_src: str | None, file_path: str
) -> list[dict[str, Any]]:
    """Diff two versions of a Python file into per-symbol SymbolDiff rows (for change_classifier).

    Conservative on failure: a syntax error in either version yields no rows (the verifier then
    treats the file as unclassifiable rather than crashing). Visibility never escalates a plain body
    edit to CONTRACT — that requires a real signature change.
    """
    try:
        before = _functions(before_src) if before_src else {}
        after = _functions(after_src) if after_src else {}
    except SyntaxError:
        return []

    # NOTE: the Phase-1 body-similarity heuristic (identity_replacement_suspected) IS active and
    # catches most "same name, wholly different function" cases. What remains deferred is robust
    # symbol identity / rename tracking: symbols are still matched by qualified name, so a
    # nested/closure function replaced by a different function of the same qualified name is matched
    # as "modified" — a narrow residual false-negative. Full identity tracking is a later enrichment.
    rows: list[dict[str, Any]] = []
    for qual in sorted(set(before) | set(after)):
        b = before.get(qual)
        a = after.get(qual)
        symbol_id = f"{file_path}::{qual}"
        if b is not None and a is not None:
            # signature = parameters AND return annotation (ast args node excludes `returns`)
            _none = ast.Constant(value=None)
            sig_changed = ast.dump(b.args) != ast.dump(a.args) or (  # type: ignore[attr-defined]
                ast.dump(b.returns or _none) != ast.dump(a.returns or _none)  # type: ignore[attr-defined]
            )
            # compare bodies with the leading docstring stripped — a docstring-only edit is cosmetic
            b_stmts = _strip_docstring(b.body)  # type: ignore[attr-defined]
            a_stmts = _strip_docstring(a.body)  # type: ignore[attr-defined]
            body_changed = ast.dump(ast.Module(body=b_stmts, type_ignores=[])) != ast.dump(
                ast.Module(body=a_stmts, type_ignores=[])
            )
            cf_changed = _control_flow_count(b) != _control_flow_count(a)
            # M4: same name + same signature + body wholly rewritten = suspected identity replacement
            identity_suspected = False
            if not sig_changed and body_changed:
                b_body, a_body = _body_source(before_src, b_stmts), _body_source(after_src, a_stmts)
                if b_body and a_body:
                    ratio = difflib.SequenceMatcher(None, b_body, a_body).ratio()
                    identity_suspected = ratio < _IDENTITY_REPLACEMENT_THRESHOLD
            if sig_changed or body_changed or cf_changed:
                rows.append(_row(symbol_id, qual, signature_changed=sig_changed,
                                 body_changed=body_changed, control_flow_changed=cf_changed,
                                 identity_replacement_suspected=identity_suspected))
        else:
            # added or removed symbol: treat as a body change
            rows.append(_row(symbol_id, qual, signature_changed=False, body_changed=True,
                             control_flow_changed=False))

    # Module/class-level semantic fallback: if no function row captured the change but the normalized
    # module AST (docstrings stripped) differs, the edit was outside function bodies — a module
    # constant, import, class attribute/base, decorator, or one-sided module add/delete. Emit a
    # module-scope BEHAVIORAL row so it is not mistaken for cosmetic. A docstring/comment/whitespace
    # only edit leaves the normalized content empty/equal.
    if not rows:
        module_changed = False
        if before_src and after_src:
            module_changed = _normalized_module_dump(before_src) != _normalized_module_dump(after_src)
        elif before_src or after_src:
            module_changed = _has_semantic_module_content(before_src or after_src)
        if module_changed:
            rows.append(_row(f"{file_path}::__module__", "__module__", signature_changed=False,
                             body_changed=True, control_flow_changed=False))
    return rows


class AstDiffAdapter:
    def __init__(self, symbol_diff_evidence: dict[str, Any] | None = None) -> None:
        self._evidence = symbol_diff_evidence

    def symbol_diff(self, action: CandidateAction, repo_root: str) -> SymbolDiffEvidence:
        if self._evidence:
            base = SymbolDiffEvidence(
                **{k: v for k, v in self._evidence.items() if k in _ALLOWED}
            )
        else:
            base = SymbolDiffEvidence(
                parsed_patch_available=False,
                changed_symbols=list(action.affected_symbols),
                max_change_kind="UNKNOWN",
                fallback_reason="no symbol diff supplied; Phase-0 cold-start file/path-level risk",
            )
        return _detect_file_operation(base, action)


def _detect_file_operation(
    base: SymbolDiffEvidence, action: CandidateAction
) -> SymbolDiffEvidence:
    """Set the FileOperationKind axis from patch headers. Independent of max_change_kind (symbol
    semantics): a deleted file and a contract change are recorded separately. Supplied evidence that
    already set a non-NONE op is preserved."""
    if base.file_operation_kind != "NONE" or not action.proposed_patch:
        return base
    ops = parse_patch_headers(action.proposed_patch)
    if not ops:
        return base
    dominant = max(ops, key=lambda o: _FILE_OP_SEVERITY.get(o.kind, 0))
    paths = tuple(
        p for o in ops if o.kind == dominant.kind and (p := (o.old_path or o.new_path)) is not None
    )
    return replace(base, file_operation_kind=dominant.kind, file_operation_paths=paths)
