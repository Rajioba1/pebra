"""Boundary-local twin of production's strict unified-diff path extraction.

The assay cannot import :mod:`pebra`, so parity is pinned from ``tests/``. Keep accepted syntax and
fail-closed behavior aligned with ``pebra.core.patch_paths``.
"""

from __future__ import annotations

import re

_PATH_TOKEN = r'("(?:\\.|[^"\\])*"|\S+)'
_DIFF_GIT = re.compile(rf"^diff --git {_PATH_TOKEN} {_PATH_TOKEN}$")
_OLD_FILE = re.compile(r"^--- (.+)$")
_NEW_FILE = re.compile(r"^\+\+\+ (.+)$")
_RENAME_FROM = re.compile(r"^rename from (.*)$")
_RENAME_TO = re.compile(r"^rename to (.*)$")
_COPY_FROM = re.compile(r"^copy from (.*)$")
_COPY_TO = re.compile(r"^copy to (.*)$")
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def _decode_git_path(raw: str) -> str:
    value = raw.split("\t", 1)[0]
    if not (value.startswith('"') and value.endswith('"')):
        return value
    content = value[1:-1]
    decoded = bytearray()
    escapes = {
        "a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13,
        '"': 34, "\\": 92,
    }
    index = 0
    while index < len(content):
        char = content[index]
        if char != "\\":
            decoded.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(content):
            return ""
        if content[index] in "01234567":
            end = index
            while end < min(len(content), index + 3) and content[end] in "01234567":
                end += 1
            decoded.append(int(content[index:end], 8))
            index = end
            continue
        escaped = escapes.get(content[index])
        if escaped is None:
            return ""
        decoded.append(escaped)
        index += 1
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _parse_diff_header(line: str) -> tuple[str, str] | None:
    match = _DIFF_GIT.match(line)
    if match:
        old = _decode_git_path(match.group(1))
        new = _decode_git_path(match.group(2))
    else:
        unquoted = _parse_unquoted_diff_header(line)
        if unquoted is None:
            return None
        old, new = unquoted
    if not old.startswith("a/") or not new.startswith("b/"):
        return None
    return old[2:], new[2:]


def _parse_unquoted_diff_header(line: str) -> tuple[str, str] | None:
    prefix = "diff --git "
    if not line.startswith(prefix):
        return None
    value = line[len(prefix):]
    candidates: list[tuple[str, str]] = []
    offset = 0
    while (separator := value.find(" b/", offset)) >= 0:
        old, new = value[:separator], value[separator + 1:]
        if old.startswith("a/") and new.startswith("b/"):
            candidates.append((old, new))
        offset = separator + 1

    matching = [pair for pair in candidates if pair[0][2:] == pair[1][2:]]
    if len(matching) == 1:
        return matching[0]
    if not matching and len(candidates) == 1:
        return candidates[0]
    return None


def touched_files(patch: str) -> tuple[str, ...]:
    if any(line.startswith("*** ") for line in patch.splitlines()):
        return ()
    paths: set[str] = set()
    current: tuple[str, str] | None = None
    old_seen = new_seen = False
    saw_diff_header = False
    plain_old: str | None = None
    hunk_old = hunk_new = 0

    for line in patch.splitlines():
        if hunk_old > 0 or hunk_new > 0:
            if line.startswith("\\"):
                continue
            if line.startswith(" ") or line == "":
                hunk_old -= 1
                hunk_new -= 1
            elif line.startswith("-"):
                hunk_old -= 1
            elif line.startswith("+"):
                hunk_new -= 1
            else:
                return ()
            if hunk_old < 0 or hunk_new < 0:
                return ()
            continue
        if match := _HUNK.match(line):
            hunk_old = int(match.group(1) or 1)
            hunk_new = int(match.group(2) or 1)
            continue
        header = _parse_diff_header(line)
        if header:
            if current is not None and old_seen != new_seen:
                return ()
            current = header
            saw_diff_header = True
            old_seen = new_seen = False
            paths.update(current)
            continue
        if line.startswith("diff --git "):
            return ()
        if match := _OLD_FILE.match(line):
            value = _decode_git_path(match.group(1))
            if not saw_diff_header:
                if plain_old is not None or (value != "/dev/null" and not value.startswith("a/")):
                    return ()
                plain_old = value
            else:
                if current is None or old_seen:
                    return ()
                if value not in {f"a/{current[0]}", "/dev/null"}:
                    return ()
                old_seen = True
        elif match := _NEW_FILE.match(line):
            value = _decode_git_path(match.group(1))
            if not saw_diff_header:
                if plain_old is None or (value != "/dev/null" and not value.startswith("b/")):
                    return ()
                if plain_old != "/dev/null":
                    paths.add(plain_old[2:])
                if value != "/dev/null":
                    paths.add(value[2:])
                plain_old = None
            else:
                if current is None or new_seen:
                    return ()
                if value not in {f"b/{current[1]}", "/dev/null"}:
                    return ()
                new_seen = True
        elif match := _RENAME_FROM.match(line):
            if current is None or _decode_git_path(match.group(1)) != current[0]:
                return ()
        elif match := _RENAME_TO.match(line):
            if current is None or _decode_git_path(match.group(1)) != current[1]:
                return ()
        elif match := _COPY_FROM.match(line):
            if current is None or _decode_git_path(match.group(1)) != current[0]:
                return ()
        elif match := _COPY_TO.match(line):
            if current is None or _decode_git_path(match.group(1)) != current[1]:
                return ()
    if plain_old is not None or (current is not None and old_seen != new_seen):
        return ()
    return tuple(sorted(paths))
