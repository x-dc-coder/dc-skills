"""Safety scanning — classify sensitive files, high-risk patterns."""
from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, Iterable, List

# ── pattern constants ──────────────────────────────────────────────────
HIGH_RISK_PATTERNS = [
    ".env", ".env.*", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "*.pem", "*id_ed25519*", "*id_rsa*",
    "secrets*.json", "credentials*.json", "service-account*.json",
]
WARNING_PATTERNS = [
    "application-local.yml", "application-local.yaml",
    "application-dev.yml", "application-dev.yaml",
    "application-prod.yml", "application-prod.yaml",
    "application-local.properties", "application-dev.properties",
    "application-prod.properties",
]


def walk_project_files(project: Path) -> Iterable[Path]:
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d != ".git"]
        root_path = Path(root)
        for name in files:
            yield root_path / name


def gitignore_match(project: Path, path: Path) -> bool:
    """Check if a file is already matched by a gitignore rule."""
    from .git import run as _run  # avoid top-level circular import
    rel = str(path.relative_to(project))
    return _run(["git", "check-ignore", "--no-index", rel], cwd=project, check=False).returncode == 0


def classify_sensitive_files(project: Path) -> Dict[str, List[str]]:
    """Scan project tree for high-risk and warning-level files."""
    from .git import is_git_repo as _is_git_repo  # avoid top-level circular import

    high_risk: List[str] = []
    warnings: List[str] = []
    is_repo = _is_git_repo(project)
    for file_path in walk_project_files(project):
        rel = str(file_path.relative_to(project))
        if rel.endswith((".example", ".sample", ".template")) or file_path.name.endswith((".example", ".sample", ".template")):
            continue
        for pattern in HIGH_RISK_PATTERNS:
            if fnmatch(rel, pattern) or fnmatch(file_path.name, pattern):
                if is_repo:
                    if not gitignore_match(project, file_path):
                        high_risk.append(rel)
                else:
                    high_risk.append(rel)
                break
        for pattern in WARNING_PATTERNS:
            if fnmatch(rel, pattern) or fnmatch(file_path.name, pattern):
                if is_repo:
                    if not gitignore_match(project, file_path):
                        warnings.append(rel)
                else:
                    warnings.append(rel)
                break
    return {"high_risk": sorted(set(high_risk)), "warnings": sorted(set(warnings))}
