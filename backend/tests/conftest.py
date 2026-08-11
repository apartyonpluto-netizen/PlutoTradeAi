from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# Every backend module resolves PLUTO_DATA_DIR (and CREDENTIAL_ENCRYPTION_KEY,
# FLASK_SECRET_KEY) from the environment at import time, not lazily - these
# must be set before anything under backend/ gets imported for the first
# time in this process, which is why this happens at module load here rather
# than inside a fixture.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("PLUTO_DATA_DIR", tempfile.mkdtemp(prefix="plutotrade_test_"))
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "pytest-fixed-test-key-not-for-real-use")
os.environ.setdefault("FLASK_SECRET_KEY", "pytest-fixed-flask-secret")
os.environ.setdefault("CRON_SECRET", "pytest-fixed-cron-secret")
os.environ.setdefault("FLASK_DEBUG", "0")


@pytest.fixture
def user_id() -> str:
    """A fresh random user id per test - every backend data module keys
    storage by user_id under PLUTO_DATA_DIR/users/<id>/, so distinct ids
    give each test its own isolated slice of the same shared temp data
    directory without needing to reload modules or swap DATA_DIR per test."""
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def other_user_id() -> str:
    """A second, distinct user id for tenant-isolation tests."""
    return f"test-{uuid.uuid4().hex[:12]}"
