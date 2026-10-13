from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import backups
import offsite_backup


# AWS docs, "Authenticating Requests: Using the Authorization Header
# (AWS Signature Version 4)", GET Object example.
_AWS_GET_OBJECT_SIGNATURE = "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"


def test_sigv4_matches_the_aws_get_object_example():
    header = offsite_backup.authorization_header(
        method="GET",
        canonical_uri="/test.txt",
        host="examplebucket.s3.amazonaws.com",
        body=b"",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        amz_date="20130524T000000Z",
        extra_headers={"range": "bytes=0-9"},
    )
    assert header.endswith(f"Signature={_AWS_GET_OBJECT_SIGNATURE}")
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in header


def test_public_status_omits_secrets(monkeypatch):
    monkeypatch.setenv("PLUTO_BACKUP_S3_BUCKET", "plutotrade-backups")
    monkeypatch.setenv("PLUTO_BACKUP_S3_ACCESS_KEY_ID", "access-key-id")
    monkeypatch.setenv("PLUTO_BACKUP_S3_SECRET_ACCESS_KEY", "super-secret-value")
    monkeypatch.setenv("PLUTO_BACKUP_S3_ENDPOINT", "https://abc.r2.cloudflarestorage.com")
    monkeypatch.delenv("PLUTO_BACKUP_S3_REGION", raising=False)
    status = offsite_backup.public_status()
    assert status["configured"] is True
    assert status["region"] == "auto"
    blob = json.dumps(status)
    assert "super-secret-value" not in blob
    assert "access-key-id" not in blob


def test_push_is_a_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("PLUTO_BACKUP_S3_BUCKET", raising=False)
    monkeypatch.delenv("PLUTO_BACKUP_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("PLUTO_BACKUP_S3_SECRET_ACCESS_KEY", raising=False)
    result = offsite_backup.push_created({"created": True, "name": "pluto-data-x.tar.gz"})
    assert result["uploaded"] is False


class _Bucket(BaseHTTPRequestHandler):
    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.puts.append((self.path, body))
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        payload = self.server.objects.get(self.path, None)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


@pytest.fixture
def bucket(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Bucket)
    server.puts = []
    server.objects = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    monkeypatch.setenv("PLUTO_BACKUP_S3_BUCKET", "plutotrade-backups")
    monkeypatch.setenv("PLUTO_BACKUP_S3_ACCESS_KEY_ID", "access-key-id")
    monkeypatch.setenv("PLUTO_BACKUP_S3_SECRET_ACCESS_KEY", "super-secret-value")
    monkeypatch.setenv("PLUTO_BACKUP_S3_ENDPOINT", f"http://{host}:{port}")
    monkeypatch.setenv("PLUTO_BACKUP_S3_REGION", "auto")
    monkeypatch.setenv("PLUTO_BACKUP_S3_PREFIX", "plutotrade/")
    yield server
    server.shutdown()


def _write_local_snapshot(payload: bytes) -> str:
    backups.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = "pluto-data-20261013T000000Z.tar.gz"
    path = backups.BACKUP_DIR / name
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_suffix("").with_suffix(".json").write_text(json.dumps({"sha256": digest, "files": 1}), encoding="utf-8")
    return name


def test_push_and_download_round_trip_against_a_local_bucket(bucket, tmp_path):
    payload = b"not-a-real-broker-order"
    name = _write_local_snapshot(payload)
    created = {"created": True, "name": name}
    uploaded = offsite_backup.push_created(created)
    assert uploaded["uploaded"] is True
    assert uploaded["bucket"] == "plutotrade-backups"
    assert "super-secret-value" not in json.dumps(uploaded)
    stored = {path: body for path, body in bucket.puts}
    assert stored["/plutotrade-backups/plutotrade/" + name] == payload
    for path, body in bucket.puts:
        bucket.objects[path] = body

    dest = tmp_path / "scratch"
    downloaded = offsite_backup.download(name, dest)
    assert downloaded.read_bytes() == payload
    assert backups.DATA_DIR.resolve() not in downloaded.resolve().parents


def test_download_refuses_a_bad_name(bucket, tmp_path):
    with pytest.raises(ValueError):
        offsite_backup.download("../pluto-data-x.tar.gz", tmp_path)


def test_restore_checks_json_and_refuses_the_live_directory(tmp_path):
    archive = tmp_path / "pluto-data-20261013T000000Z.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = b'{"account": "paper"}'
        info = tarfile.TarInfo("users/a/account.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    archive.write_bytes(buf.getvalue())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    dest = tmp_path / "restored"
    result = offsite_backup.restore_archive(archive, dest, {"sha256": digest})
    assert result["ok"] is True
    assert json.loads((dest / "users/a/account.json").read_text())["account"] == "paper"

    bad = offsite_backup.restore_archive(archive, tmp_path / "other", {"sha256": "0" * 64})
    assert bad["ok"] is False
    assert "checksum" in bad["error"]

    with pytest.raises(ValueError):
        offsite_backup.restore_archive(archive, backups.DATA_DIR)
    with pytest.raises(ValueError):
        offsite_backup.restore_archive(archive, backups.DATA_DIR / "nested")


def test_restore_refuses_links_and_path_traversal(tmp_path):
    archive = tmp_path / "pluto-data-escape.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("escape.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "elsewhere"
        tar.addfile(info)
    archive.write_bytes(buf.getvalue())
    with pytest.raises(ValueError, match="link"):
        offsite_backup.restore_archive(archive, tmp_path / "out")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = b"{}"
        info = tarfile.TarInfo("../outside.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    archive.write_bytes(buf.getvalue())
    with pytest.raises(ValueError, match="path"):
        offsite_backup.restore_archive(archive, tmp_path / "out2")
    assert not (tmp_path / "outside.json").exists()
