from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import InvalidToken

import crypto_utils  # noqa: E402

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PLUTO_DATA_DIR", str(BASE_DIR / "data"))).resolve()
USER_DATA_ROOT = DATA_DIR / "users"

# (filename, [fields to rotate])
CREDENTIAL_FILES = [
    ("webull_credentials.json", ["app_key", "app_secret"]),
    ("anthropic_credentials.json", ["api_key"]),
]


def _rotate_file(path: Path, fields: list[str], old_key: str, new_key: str, apply: bool) -> int:
    """Returns the number of fields actually re-encrypted in this file.
    Never prints a plaintext credential value - only counts and filenames."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return 0
    if not isinstance(data, dict):
        return 0

    changed = 0
    for field in fields:
        raw = str(data.get(field, ""))
        if not raw or not crypto_utils.is_encrypted(raw):
            continue  # empty or legacy plaintext - nothing to rotate, next normal read will encrypt it under the new key
        plaintext = crypto_utils.decrypt_with_key(raw, old_key)
        new_encrypted = crypto_utils.encrypt_with_key(plaintext, new_key)
        # Round-trip check before trusting the new value - never leave a file
        # holding a token this process can't prove decrypts correctly. A
        # decrypt failure here (InvalidToken) is just as much a "don't write
        # this" signal as a value mismatch, so both collapse to the same error.
        try:
            round_tripped = crypto_utils.decrypt_with_key(new_encrypted, new_key)
        except InvalidToken:
            round_tripped = None
        if round_tripped != plaintext:
            raise RuntimeError(f"Round-trip verification failed for {field} in {path} - aborting without writing.")
        data[field] = new_encrypted
        changed += 1

    if changed and apply:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-encrypts every stored Webull/Anthropic credential from OLD_KEY to NEW_KEY. "
            "Run with --dry-run first (the default) to see what would change, then --apply to write it. "
            "Never pass keys as CLI args (they'd land in shell history) - use the env vars."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Actually write the re-encrypted files. Without this, it's a dry run.")
    args = parser.parse_args()

    old_key = os.environ.get("OLD_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    new_key = os.environ.get("NEW_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not old_key or not new_key:
        print("Set OLD_CREDENTIAL_ENCRYPTION_KEY and NEW_CREDENTIAL_ENCRYPTION_KEY env vars first.", file=sys.stderr)
        sys.exit(1)
    if old_key == new_key:
        print("Old and new key are identical - nothing to rotate.", file=sys.stderr)
        sys.exit(1)

    if not USER_DATA_ROOT.exists():
        print(f"No user data directory found at {USER_DATA_ROOT}", file=sys.stderr)
        sys.exit(1)

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] Rotating credentials under {USER_DATA_ROOT}")

    total_files = 0
    total_fields = 0
    for user_dir in sorted(USER_DATA_ROOT.iterdir()):
        if not user_dir.is_dir():
            continue
        for filename, fields in CREDENTIAL_FILES:
            path = user_dir / filename
            if not path.exists():
                continue
            try:
                changed = _rotate_file(path, fields, old_key, new_key, apply=args.apply)
            except RuntimeError as error:
                print(f"  ABORTED: {error}", file=sys.stderr)
                sys.exit(1)
            if changed:
                total_files += 1
                total_fields += changed
                print(f"  {user_dir.name}/{filename}: {changed} field(s) {'rotated' if args.apply else 'would rotate'}")

    print(f"[{mode}] {total_fields} field(s) across {total_files} file(s).")
    if not args.apply:
        print("Dry run only - re-run with --apply to write changes.")
    else:
        print("Done. Set CREDENTIAL_ENCRYPTION_KEY to the new key everywhere (Render env var) before the next deploy/restart.")


if __name__ == "__main__":
    main()
