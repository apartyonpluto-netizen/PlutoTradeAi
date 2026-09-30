"""Compressed snapshots of the app's data directory (PLUTO_DATA_DIR).

What this protects against: a corrupted or wrongly rewritten file, a bad
deploy, an accidental deletion - anything that damages records while the
disk itself survives. What it does NOT protect against: loss of the disk.
For that an admin downloads a snapshot (Admin -> Backups) and keeps it
elsewhere; off-site storage would need infrastructure the owner has not
authorized. Restores are manual and documented in docs/RUNBOOK_BACKUP_RESTORE.md
- there is deliberately no endpoint that overwrites live records.

Snapshots contain credentials only in their encrypted form (see
docs/SECURITY.md); treat downloaded files as sensitive anyway."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))).resolve()
BACKUP_DIR = DATA_DIR / "backups"
KEEP = 7
MIN_FREE_BYTES = 150 * 1024 * 1024
MAX_AGE_SECONDS = 20 * 3600
NAME_PREFIX = "pluto-data-"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _excluded(path: Path) -> bool:
    parts = path.relative_to(DATA_DIR).parts
    return bool(parts) and (parts[0] == "backups" or parts[0].startswith("pre-restore-")
                            or path.name.endswith(".lock") or ".tmp" in path.name)


def list_backups() -> List[Dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    out = []
    for path in sorted(BACKUP_DIR.glob(f"{NAME_PREFIX}*.tar.gz"), reverse=True):
        manifest = _manifest_for(path)
        out.append({"name": path.name, "bytes": path.stat().st_size, "created_at": manifest.get("created_at"),
                    "files": manifest.get("files"), "sha256": manifest.get("sha256")})
    return out


def _manifest_for(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.with_suffix("").with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def path_for(name: str) -> Optional[Path]:
    """The backup file for `name`, only if it is one of ours (no traversal)."""
    candidate = (BACKUP_DIR / name).resolve()
    if candidate.parent != BACKUP_DIR.resolve() or not candidate.name.startswith(NAME_PREFIX) or not candidate.exists():
        return None
    return candidate


def create(reason: str = "") -> Dict[str, Any]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(BACKUP_DIR).free
    if free < MIN_FREE_BYTES:
        return {"created": False, "reason": f"only {free // (1024 * 1024)} MB free - not writing a backup"}
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    target = BACKUP_DIR / f"{NAME_PREFIX}{stamp}.tar.gz"
    partial = target.with_name(target.name + ".partial")
    files = 0
    with tarfile.open(partial, "w:gz") as archive:
        for path in sorted(DATA_DIR.rglob("*")):
            if path.is_file() and not _excluded(path):
                archive.add(path, arcname=str(path.relative_to(DATA_DIR)))
                files += 1
    digest = hashlib.sha256(partial.read_bytes()).hexdigest()
    os.replace(partial, target)
    manifest = {"created_at": _now().isoformat(), "files": files, "sha256": digest, "reason": reason, "bytes": target.stat().st_size}
    target.with_suffix("").with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
    for old in list_backups()[KEEP:]:
        (BACKUP_DIR / old["name"]).unlink(missing_ok=True)
        (BACKUP_DIR / old["name"]).with_suffix("").with_suffix(".json").unlink(missing_ok=True)
    return {"created": True, "name": target.name, **manifest}


def verify(name: str) -> Dict[str, Any]:
    """Reads a backup without extracting it: checksum, and every .json member parses."""
    path = path_for(name)
    if path is None:
        return {"ok": False, "error": "no such backup"}
    manifest = _manifest_for(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    bad: List[str] = []
    members = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            members += 1
            if member.isfile() and member.name.endswith(".json"):
                try:
                    json.loads(archive.extractfile(member).read().decode("utf-8"))
                except (ValueError, UnicodeDecodeError, AttributeError):
                    bad.append(member.name)
    ok = digest == manifest.get("sha256") and not bad
    return {"ok": ok, "checksum_matches": digest == manifest.get("sha256"), "members": members, "unreadable_json": bad}


def due() -> bool:
    latest = next(iter(list_backups()), None)
    if not latest or not latest.get("created_at"):
        return True
    try:
        return (_now() - datetime.fromisoformat(latest["created_at"])).total_seconds() > MAX_AGE_SECONDS
    except ValueError:
        return True
