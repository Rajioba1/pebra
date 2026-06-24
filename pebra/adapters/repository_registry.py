"""repository_registry (Architecture §3, AD-24) — resolve repo root + stable repo_id, init .pebra/.

Adapter implementing ``RepositoryRegistryPort``. The repo_id is a deterministic hash of the resolved
absolute root path so the same repo always maps to the same scoped state.
"""

from __future__ import annotations

import hashlib

from pebra.adapters import paths
from pebra.ports.repository_registry_port import RepoMetadata


class RepositoryRegistry:
    def resolve(self, start_path: str) -> RepoMetadata:
        root = paths.find_repo_root(start_path)
        paths.ensure_pebra_dir(root)
        repo_id = "repo_" + hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]
        return RepoMetadata(repo_id=repo_id, repo_root=str(root))
