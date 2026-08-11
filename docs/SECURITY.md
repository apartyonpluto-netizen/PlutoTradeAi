# Credential security

## What's encrypted

`webull_credentials.json` (`app_key`, `app_secret`) and `anthropic_credentials.json`
(`api_key`), one file per user under `PLUTO_DATA_DIR/users/<user_id>/`, are encrypted
at rest with Fernet (`backend/crypto_utils.py`). Encrypted values carry an `enc:v1:`
prefix; anything without that prefix is treated as a legacy plaintext value written
before encryption existed, decrypted transparently, and silently re-encrypted the next
time it's read (`get_webull_credentials`, `get_anthropic_api_key`).

## Where the key lives

`CREDENTIAL_ENCRYPTION_KEY` is a 32-byte hex string, SHA-256'd down to a Fernet key at
use time (`crypto_utils._fernet`). It is supplied entirely separately from the
credential files it protects:

- **Production (Render):** `render.yaml` sets it with `generateValue: true` - Render
  generates it once at first deploy and stores it in its own environment-variable
  store, never in a file this repo touches.
- **Local dev:** falls back to `data/.credential_encryption_key`, a file generated on
  first run. That path is git-ignored - confirmed via `git check-ignore`.
- It is never logged. The only place `crypto_utils` references the key in a log
  message is a generic warning on decrypt failure ("CREDENTIAL_ENCRYPTION_KEY may have
  changed") that never includes the key's value.
- It is never committed. `.env.example` ships with the field blank.

**Losing this key is unrecoverable, by design.** `decrypt()` fails closed - a wrong or
missing key returns `""` and logs a warning rather than raising into a 500. If the key
is lost, every stored credential has to be re-entered by hand in Account Hub; there is
no way to recover the plaintext from the encrypted files alone.

## Backup plan

The key exists in exactly one durable place: Render's environment-variable store for
this service. That store is the thing to protect, not a copy of the key itself -
treat "losing access to the Render account" as the actual disaster scenario (keep
billing/account recovery current, make sure more than one person has account access
if that's ever a concern). If a physical backup is wanted, save it in a password
manager, not in this repo or any log/chat.

## Rotation plan

Fernet uses one key for both directions - rotating it means every previously-encrypted
value needs to be re-encrypted under the new key before the old one is discarded, or
it becomes permanently undecryptable. Do not just swap the env var and redeploy; that
orphans every existing credential.

`backend/scripts/rotate_credential_key.py` automates this safely:

1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Dry run first (default mode - writes nothing):
   ```
   OLD_CREDENTIAL_ENCRYPTION_KEY=<current key> \
   NEW_CREDENTIAL_ENCRYPTION_KEY=<new key> \
   PLUTO_DATA_DIR=/var/data \
   python backend/scripts/rotate_credential_key.py
   ```
3. Review the field/file counts it reports, then apply:
   ```
   ... python backend/scripts/rotate_credential_key.py --apply
   ```
   Each field is re-encrypted and round-trip verified under the new key before being
   written; if verification fails for any field, the whole run aborts without writing
   anything (see `_rotate_file` / `test_credential_rotation.py`).
4. Only after `--apply` succeeds, update `CREDENTIAL_ENCRYPTION_KEY` in Render's
   environment settings to the new key and redeploy/restart. Doing this before step 3
   would leave the running app unable to decrypt anything until the rotation finishes.
5. Never pass either key as a CLI argument (shell history) - env vars only, and clear
   your shell history/scrollback afterward if you typed them interactively.

Covered by `backend/tests/test_credential_rotation.py`: dry-run leaves files untouched,
apply rotates and the old key stops working afterward, empty/legacy-plaintext fields
are left alone, and a failed round-trip aborts without a partial write.

## Incident record: SDK log exposure (found and fixed 2026-08-10)

The Webull OpenAPI Python SDK's own logger (`webull.core.http.*`) wrote every request's
headers to `backend/webull_trade_sdk.log` at INFO level, in plaintext, including
`x-app-key` and `x-signature` (an HMAC of the request derived from the app secret).
This was the SDK's default behavior, not something this app's code did on purpose.

**What was exposed:** `x-app-key` (the real Webull sandbox app key identifier) and
`x-signature` values, repeated across roughly a week of accumulated log output
(~1MB). The raw `app_secret` itself was never present in the log - only key-derived
per-request signatures.

**Where it was exposed:** Only on disk (local dev machine and the Render persistent
disk). `*.log` is git-ignored, confirmed via `git check-ignore` - it was never
committed and never reached the public GitHub repo.

**Fix:** `backend/integrations/webull.py` now installs a `logging.Filter` on the SDK's
log handler (`_redact_webull_sdk_logging`) that regex-redacts any field whose name
contains `app.?key`, `signature`, `secret`, `token`, `authorization`, or `password`
before the line is written. Covered by `backend/tests/test_webull_log_redaction.py`,
including a test that drives a real (mocked-transport) SDK call and asserts nothing
sensitive lands in the file. The fix also happened to close a second bug: the SDK was
attaching a fresh log handler on every single API call instead of reusing one, so the
file was growing unbounded with duplicate lines independent of the exposure issue.

**Historical file cleanup:** The pre-fix log content still sits on the production
Render disk and needs to be inspected and cleared via Render's Shell - blocked on
Render account access as of this writing. See the credential-rotation risk assessment
below for whether that residual exposure changes anything for the live Webull
sandbox credentials.

## Risk assessment: does the log exposure require rotating the live Webull credentials

Sandbox (paper-trading) credentials only - no real money or real brokerage account is
reachable through them.

- `x-signature` is an HMAC over the request, not the app secret itself. HMAC is
  specifically designed to resist exactly this "known signature, recover the key"
  attack - its presence in the log does not hand over the app secret.
- Replay risk is the more realistic concern: the signed requests include a nonce and
  ISO-8601 timestamp (`webull/trade/events/signature_composer.py`), which is the
  standard shape of anti-replay protection, but this app has not independently
  verified how strictly Webull's server enforces that window.
- `x-app-key` (the identifier, not a secret by itself) was exposed directly and
  repeatedly in plaintext.

**Recommendation: rotate the real Webull sandbox app key/secret anyway.** The
cryptographic exposure is low-severity, but the identifier was exposed outright, the
exact server-side replay window isn't independently confirmed, and this is a sandbox
credential - the cost of rotating is a few minutes in Webull's developer portal and
re-entering it in Account Hub, against a real (if small) residual exposure. Rotating
sandbox credentials is a third-party account action - generate the new app key/secret
in Webull's developer portal, then update it in Account Hub; nothing else needs to
change since credentials are already looked up per-request, not cached at process
start (`swapping to a different app key` handling in `webull_credentials.py` already
covers dropping the stale seed balance when this happens).
