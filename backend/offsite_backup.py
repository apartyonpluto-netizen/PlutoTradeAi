"""Copy a local data snapshot off the Render disk.

The disk snapshot in backups.py dies with the disk. This module uploads
that same tar.gz (and its manifest) to an S3-compatible bucket the owner
provisions — Cloudflare R2's free tier is enough — and can download it
back into a scratch directory. It never writes into the live data
directory. Credentials are read from the environment and are not logged.

Unset credentials are a no-op: local snapshots still work, and status
reports that off-site storage is not configured.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

import requests

import backups

_SERVICE = "s3"


def _env(name: str) -> str:
    import os
    return os.environ.get(name, "").strip()


def settings() -> Dict[str, str]:
    endpoint = _env("PLUTO_BACKUP_S3_ENDPOINT") or "https://s3.amazonaws.com"
    region = _env("PLUTO_BACKUP_S3_REGION")
    if not region:
        region = "auto" if "r2.cloudflarestorage.com" in endpoint else "us-east-1"
    return {
        "bucket": _env("PLUTO_BACKUP_S3_BUCKET"),
        "access_key": _env("PLUTO_BACKUP_S3_ACCESS_KEY_ID"),
        "secret_key": _env("PLUTO_BACKUP_S3_SECRET_ACCESS_KEY"),
        "endpoint": endpoint,
        "region": region,
        "prefix": _env("PLUTO_BACKUP_S3_PREFIX") or "plutotrade/",
    }


def configured() -> bool:
    current = settings()
    return bool(current["bucket"] and current["access_key"] and current["secret_key"])


def public_status() -> Dict[str, Any]:
    """Safe to return from an admin API: no keys."""
    current = settings()
    host = urlparse(current["endpoint"]).netloc
    return {
        "configured": configured(),
        "bucket": current["bucket"] if configured() else "",
        "endpoint_host": host if configured() else "",
        "region": current["region"] if configured() else "",
        "prefix": current["prefix"] if configured() else "",
    }


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str, service: str = _SERVICE) -> bytes:
    dated = _sign(f"AWS4{secret}".encode("utf-8"), date_stamp)
    regional = _sign(dated, region)
    scoped = _sign(regional, service)
    return _sign(scoped, "aws4_request")


def authorization_header(
    *,
    method: str,
    canonical_uri: str,
    host: str,
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    amz_date: str,
    query: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
    service: str = _SERVICE,
) -> str:
    """AWS Signature Version 4. canonical_uri is the path only, already
    escaped, beginning with /. extra_headers are signed in addition to
    host, x-amz-content-sha256, and x-amz-date."""
    payload_hash = hashlib.sha256(body).hexdigest()
    date_stamp = amz_date[:8]
    header_map = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    for key, value in (extra_headers or {}).items():
        header_map[key.lower()] = value
    names = sorted(header_map)
    signed_headers = ";".join(names)
    # Header values are trimmed and internal runs of whitespace collapsed.
    # The headers block already ends in a newline; the blank line before
    # SignedHeaders is required (AWS's GET Object example hashes that form).
    canonical_headers = "".join(
        f"{name}:{' '.join(str(header_map[name]).split())}\n" for name in names
    )
    canonical_request = "\n".join([
        method,
        canonical_uri,
        query,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        _signing_key(secret_key, date_stamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def _object_request(method: str, key: str, body: bytes = b"") -> requests.Response:
    current = settings()
    if not configured():
        raise RuntimeError("off-site backup is not configured")
    endpoint = current["endpoint"].rstrip("/")
    host = urlparse(endpoint).netloc
    object_key = f"{current['prefix']}{key}".lstrip("/")
    canonical_uri = "/" + quote(current["bucket"], safe="") + "/" + quote(object_key, safe="/")
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload_hash = hashlib.sha256(body).hexdigest()
    auth = authorization_header(
        method=method,
        canonical_uri=canonical_uri,
        host=host,
        body=body,
        access_key=current["access_key"],
        secret_key=current["secret_key"],
        region=current["region"],
        amz_date=amz_date,
    )
    response = requests.request(
        method,
        endpoint + canonical_uri,
        data=body,
        headers={
            "Host": host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": auth,
        },
        timeout=60,
        allow_redirects=False,
    )
    return response


def push_created(created: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Upload one backups.create() result. Never raises for a missing
    configuration or a transport error — the caller already has the local
    snapshot."""
    if not created or not created.get("created"):
        return {"uploaded": False, "reason": "no local snapshot"}
    if not configured():
        return {"uploaded": False, "reason": "off-site backup is not configured"}
    name = str(created["name"])
    path = backups.path_for(name)
    if path is None:
        return {"uploaded": False, "reason": "local snapshot was not found"}
    try:
        archive_response = _object_request("PUT", name, path.read_bytes())
        if archive_response.status_code not in (200, 201):
            return {"uploaded": False, "reason": f"bucket returned HTTP {archive_response.status_code}"}
        manifest_path = path.with_suffix("").with_suffix(".json")
        if manifest_path.exists():
            manifest_response = _object_request("PUT", manifest_path.name, manifest_path.read_bytes())
            if manifest_response.status_code not in (200, 201):
                return {"uploaded": False, "reason": f"manifest upload returned HTTP {manifest_response.status_code}", "name": name}
        return {"uploaded": True, "name": name, **{k: public_status()[k] for k in ("bucket", "endpoint_host")}}
    except requests.RequestException as error:
        return {"uploaded": False, "reason": f"upload failed: {type(error).__name__}"}


def download(name: str, dest_dir: Path) -> Path:
    """Download one snapshot into dest_dir. dest_dir must not be the live
    data directory. Returns the archive path."""
    if not backups.NAME_PREFIX or not name.startswith(backups.NAME_PREFIX) or "/" in name or ".." in name:
        raise ValueError("refusing that backup name")
    dest_dir = dest_dir.resolve()
    _refuse_live(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    response = _object_request("GET", name)
    if response.status_code != 200:
        raise ValueError(f"bucket returned HTTP {response.status_code}")
    target = dest_dir / name
    target.write_bytes(response.content)
    return target


def _refuse_live(dest: Path) -> None:
    data = backups.DATA_DIR.resolve()
    dest = dest.resolve()
    if dest == data or data in dest.parents or dest in data.parents:
        raise ValueError("refusing to write a restore into the live data directory")


def restore_archive(archive: Path, dest: Path, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract a snapshot into dest and check every JSON member parses.

    Refuses the live data directory. Does not place or cancel orders.
    """
    import tarfile
    archive = archive.resolve()
    dest = dest.resolve()
    _refuse_live(dest)
    if not archive.is_file():
        raise ValueError("archive is missing")
    if manifest and manifest.get("sha256"):
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != manifest["sha256"]:
            return {"ok": False, "error": "checksum does not match the manifest", "files": 0, "unreadable_json": []}
    dest.mkdir(parents=True, exist_ok=True)
    files = 0
    bad = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f"refusing link member {member.name}")
            target = (dest / member.name).resolve()
            if target != dest and dest not in target.parents:
                raise ValueError(f"refusing path {member.name}")
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            payload = extracted.read()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            files += 1
            if member.name.endswith(".json"):
                try:
                    json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    bad.append(member.name)
    return {"ok": not bad, "files": files, "unreadable_json": bad, "dest": str(dest)}
