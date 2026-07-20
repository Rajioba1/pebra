"""Filesystem redirection checks for repository-managed adapter and CLI paths."""

from __future__ import annotations

from pathlib import Path
import stat


def is_redirect(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True


def redirected_component(root: Path, path: Path) -> Path | None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path
    current = root
    for part in relative.parts:
        current /= part
        if is_redirect(current):
            return current
    return None


def is_hardlinked_file(path: Path) -> bool:
    """Fail closed for an aliased regular file or unreadable destination metadata."""
    try:
        metadata = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1


def unsafe_managed_path(root: Path, path: Path) -> Path | None:
    """Locate a managed path redirect or hardlinked destination without following it."""
    redirect = redirected_component(root, path)
    if redirect is not None:
        return redirect
    return path if is_hardlinked_file(path) else None
