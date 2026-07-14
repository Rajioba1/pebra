"""Fail-closed language-family witnesses for structural API continuity."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ContinuityWitness:
    name: str
    version: str
    languages: frozenset[str]
    forwarder_kinds: frozenset[str]
    declaration_pattern: str
    case_sensitive: bool = True
    replace_all_identifiers: bool = False

    def _canonical_identifier_source(self, source: str, name: str) -> tuple[str, int] | None:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) is None:
            return None
        normalized = source.replace("\r\n", "\n")
        if self.name == "rust" and re.search(r"\br[#]*\"", normalized):
            return None
        comments = (
            (("//", "\n"), ("/*", "*/"))
            if self.name != "pascal"
            else (("//", "\n"), ("(*", "*)"), ("{", "}"))
        )
        quotes = {"'", '"'} | ({"`"} if self.name == "ecmascript" else set())
        out: list[str] = []
        count = 0
        index = 0
        while index < len(normalized):
            comment = next(
                ((start, end) for start, end in comments if normalized.startswith(start, index)),
                None,
            )
            if comment is not None:
                start, marker = comment
                end = normalized.find(marker, index + len(start))
                end = len(normalized) if end < 0 else end + (0 if marker == "\n" else len(marker))
                segment = normalized[index:end]
                if re.search(rf"\b{re.escape(name)}\b", segment, flags):
                    return None
                out.append(segment)
                index = end
                continue
            char = normalized[index]
            if self.name == "ecmascript" and char == "/":
                previous = next(
                    (normalized[pos] for pos in range(index - 1, -1, -1) if not normalized[pos].isspace()),
                    "",
                )
                following = next(
                    (normalized[pos] for pos in range(index + 1, len(normalized)) if not normalized[pos].isspace()),
                    "",
                )
                if previous != "<" and following != ">":
                    return None
                out.append(char)
                index += 1
                continue
            if char in quotes:
                end = index + 1
                while end < len(normalized):
                    if normalized[end] == "\\":
                        end += 2
                        continue
                    if normalized[end] == char:
                        end += 1
                        break
                    end += 1
                segment = normalized[index:end]
                if re.search(rf"\b{re.escape(name)}\b", segment, flags):
                    return None
                out.append(segment)
                index = end
                continue
            if char.isalpha() or char in {"_", "$"}:
                end = index + 1
                while end < len(normalized) and (
                    normalized[end].isalnum() or normalized[end] in {"_", "$"}
                ):
                    end += 1
                token = normalized[index:end]
                matches = token == name if self.case_sensitive else token.lower() == name.lower()
                if matches:
                    previous = next(
                        (normalized[pos] for pos in range(index - 1, -1, -1) if not normalized[pos].isspace()),
                        "",
                    )
                    following = next(
                        (normalized[pos] for pos in range(end, len(normalized)) if not normalized[pos].isspace()),
                        "",
                    )
                    if previous == "." or following == ":" and self.name != "pascal":
                        return None
                    out.append("__PEBRA_BINDING__")
                    count += 1
                else:
                    out.append(token)
                index = end
                continue
            out.append(char)
            index += 1
        return "".join(out), count

    def _canonical_implementation(self, source: str, name: str) -> str | None:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        normalized = source.replace("\r\n", "\n")
        if self.replace_all_identifiers:
            canonical = self._canonical_identifier_source(normalized, name)
            if canonical is None or canonical[1] == 0:
                return None
            return canonical[0]
        pattern = re.compile(
            self.declaration_pattern.format(name=re.escape(name)), flags
        )
        matches = list(pattern.finditer(normalized))
        if len(matches) != 1:
            return None
        match = matches[0]
        start, end = match.span("name")
        return normalized[:start] + "__PEBRA_RENAMED__" + normalized[end:]

    def same_implementation(
        self,
        old_source: str,
        target_source: str,
        old_name: str,
        target_name: str,
    ) -> bool:
        old = self._canonical_implementation(old_source, old_name)
        target = self._canonical_implementation(target_source, target_name)
        return old is not None and target is not None and old == target

    def identifier_only_migration(
        self,
        before_source: str,
        after_source: str,
        old_name: str,
        target_name: str,
    ) -> bool:
        before = self._canonical_identifier_source(before_source, old_name)
        after = self._canonical_identifier_source(after_source, target_name)
        return (
            before is not None
            and after is not None
            and before[1] > 0
            and before[1] == after[1]
            and before[0] == after[0]
        )

    def _parameter_names(self, parameters: str) -> tuple[str, ...] | None:
        if not parameters.strip():
            return ()
        names: list[str] = []
        for parameter in parameters.split(","):
            value = parameter.strip()
            if not value:
                return None
            if self.name in {"rust", "scala", "pascal"}:
                if ":" not in value:
                    return None
                candidate = value.split(":", 1)[0].strip()
            elif self.name == "go":
                candidate = value.split()[0] if len(value.split()) >= 2 else ""
            else:
                candidate = value.replace("...", " ").split()[-1]
                candidate = candidate.removesuffix("[]")
            if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", candidate) is None:
                return None
            names.append(candidate)
        return tuple(names)

    def is_safe_forwarder(self, source: str, old_name: str, target_name: str) -> bool:
        normalized = " ".join(source.replace("\r\n", "\n").split())
        old = re.escape(old_name)
        target = re.escape(target_name)
        flags = re.IGNORECASE if not self.case_sensitive else 0
        if self.name == "ecmascript":
            return re.fullmatch(
                rf"export\s+const\s+{old}\s*=\s*{target}\s*;", normalized, flags
            ) is not None
        patterns = {
            "java": rf"^.*\b{old}\s*\((?P<params>[^()]*)\)\s*\{{\s*(?:return\s+)?{target}\s*\((?P<args>[^()]*)\)\s*;\s*\}}$",
            "rust": rf"^(?:pub(?:\([^)]*\))?\s+)?fn\s+{old}\s*\((?P<params>[^()]*)\)[^{{]*\{{\s*(?:return\s+)?{target}\s*\((?P<args>[^()]*)\)\s*;?\s*\}}$",
            "go": rf"^func\s+(?:\([^)]*\)\s*)?{old}\s*\((?P<params>[^()]*)\)[^{{]*\{{\s*return\s+{target}\s*\((?P<args>[^()]*)\)\s*\}}$",
            "dart": rf"^.*\b{old}\s*\((?P<params>[^()]*)\)\s*=>\s*{target}\s*\((?P<args>[^()]*)\)\s*;$",
            "scala": rf"^def\s+{old}\s*\((?P<params>[^()]*)\)[^=]*=\s*{target}\s*\((?P<args>[^()]*)\)$",
            "pascal": rf"^function\s+{old}\s*\((?P<params>[^()]*)\)[^;]*;\s*begin\s+{old}\s*:=\s*{target}\s*\((?P<args>[^()]*)\)\s*;\s*end;$",
        }
        match = re.fullmatch(patterns[self.name], normalized, flags)
        if match is None:
            return False
        parameters = self._parameter_names(match.group("params"))
        arguments = tuple(
            argument.strip() for argument in match.group("args").split(",")
        ) if match.group("args").strip() else ()
        if not self.case_sensitive:
            parameters = tuple(name.lower() for name in parameters) if parameters is not None else None
            arguments = tuple(name.lower() for name in arguments)
        return parameters is not None and parameters == arguments

    def patch_is_exhaustive_forwarder(
        self,
        patch: str,
        old_name: str,
        target_name: str,
        forwarder_source: str,
    ) -> bool:
        del forwarder_source
        removed: list[str] = []
        added: list[str] = []
        forwarder_count = 0
        saw_block = False
        block_has_hunk = False
        in_hunk = False
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                if saw_block and not block_has_hunk:
                    return False
                saw_block = True
                block_has_hunk = False
                in_hunk = False
                continue
            if line.startswith("@@ "):
                if not saw_block:
                    return False
                block_has_hunk = True
                in_hunk = True
                continue
            if not in_hunk:
                if line.startswith(("index ", "--- ", "+++ ")) or not line.strip():
                    continue
                return False
            if line.startswith("\\ No newline at end of file") or line.startswith(" "):
                continue
            if line.startswith(("--- ", "+++ ")):
                continue
            if line.startswith("-"):
                if line[1:].strip():
                    removed.append(line[1:])
            elif line.startswith("+"):
                candidate = line[1:]
                if self.is_safe_forwarder(candidate, old_name, target_name):
                    forwarder_count += 1
                elif candidate.strip():
                    added.append(candidate)
            elif line:
                return False
        return (
            saw_block
            and block_has_hunk
            and forwarder_count == 1
            and self.identifier_only_migration(
                "\n".join(removed), "\n".join(added), old_name, target_name
            )
        )


_ECMASCRIPT = ContinuityWitness(
    name="ecmascript",
    version="1",
    languages=frozenset({"javascript", "jsx", "typescript", "tsx"}),
    forwarder_kinds=frozenset({"constant", "variable"}),
    declaration_pattern=r"\bfunction\s+(?P<name>{name})\b",
)

_WITNESSES = (
    _ECMASCRIPT,
    ContinuityWitness(
        name="java",
        version="1",
        languages=frozenset({"java"}),
        forwarder_kinds=frozenset({"method"}),
        declaration_pattern=r"\b(?P<name>{name})\s*\(",
    ),
    ContinuityWitness(
        name="rust",
        version="1",
        languages=frozenset({"rust"}),
        forwarder_kinds=frozenset({"function", "method"}),
        declaration_pattern=r"\bfn\s+(?P<name>{name})\b",
    ),
    ContinuityWitness(
        name="go",
        version="1",
        languages=frozenset({"go"}),
        forwarder_kinds=frozenset({"function", "method"}),
        declaration_pattern=r"\bfunc\s+(?P<name>{name})\b",
    ),
    ContinuityWitness(
        name="dart",
        version="1",
        languages=frozenset({"dart"}),
        forwarder_kinds=frozenset({"function", "method"}),
        declaration_pattern=r"\b(?P<name>{name})\s*\(",
    ),
    ContinuityWitness(
        name="scala",
        version="1",
        languages=frozenset({"scala"}),
        forwarder_kinds=frozenset({"method"}),
        declaration_pattern=r"\bdef\s+(?P<name>{name})\b",
    ),
)

_BY_LANGUAGE = {
    language: witness
    for witness in _WITNESSES
    for language in witness.languages
}


def witness_for_language(language: str | None) -> ContinuityWitness | None:
    return _BY_LANGUAGE.get(str(language or "").lower())
