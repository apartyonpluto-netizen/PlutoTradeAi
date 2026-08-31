from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

# Hoisted to module level (2026-08-19) - these four were previously
# function-local imports inside _call_with_429_retry, _classify_server_exception,
# _place_order_with_retry, and _fetch_order_detail, re-running the import
# statement on EVERY call even though the actual module was already cached in
# sys.modules after the first time. Found while chasing the residual OOM
# leak: a live tracemalloc snapshot on the deployed service showed
# <frozen importlib._bootstrap>/_bootstrap_external as the single largest
# still-growing allocation site by a wide margin (~1MB, 10000+ objects,
# bigger than any of this app's own code) - _fetch_order_detail alone runs
# once per transitional order on every fast-monitor/continuous-monitor pass,
# so this import statement was executing an enormous number of times.
# Exception class objects themselves are lightweight and side-effect-free to
# import eagerly, unlike the ApiClient/TradeClient SDK client construction
# (which is a genuinely heavier, credential-specific object - see
# _get_trade_client's own per-credential-pair caching instead).
from webull.core.exception import error_code
from webull.core.exception.exceptions import ClientException, ServerException

# Paper-trading OpenAPI apps authenticate against a separate sandbox host from
# the live-trading API - api.webull.com rejects sandbox credentials with a
# generic 401 "invalid credentials" error that gives no hint the real problem
# is the endpoint, not the key/secret.
_REGION_ID = "us"
_SANDBOX_ENDPOINT = "api.sandbox.webull.com"

# The webull SDK's TradeClient logs every request's headers at INFO level,
# including x-signature (an HMAC derived from the App Secret) and x-app-key
# - both landed in plaintext in webull_trade_sdk.log. It also re-attaches a
# fresh file/stream handler to the shared "webull.core" logger on every
# single API call (one new ApiClient per call, and its default-handler check
# only looks at that fresh instance), so the file was growing unbounded with
# duplicate output too. _redact_webull_sdk_logging replaces that with one
# handler, configured once, that strips sensitive header values before they
# ever reach disk.
_SENSITIVE_LOG_FIELD = re.compile(
    r'("(?:[a-z0-9_-]*(?:app.?key|signature|secret|token|authorization|password)[a-z0-9_-]*)"\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)
_webull_sdk_logging_configured = False

# The continuous-monitor tick (every ~10s, around the clock, for every
# Webull-connected user - see continuous_monitor_worker.py) was rebuilding a
# fresh ApiClient/TradeClient (SignerFactory, DefaultEndpointResolver, retry
# policy, etc.) on EVERY single webull_api call - several per user per tick.
# That sustained allocation churn, running 24/7 regardless of market hours,
# matched the web service's observed memory-climbs-until-OOM-kill pattern on
# Render. Credentials (app_key, app_secret) are static per connected account,
# so it's safe to build the client graph once per credential pair and reuse
# it - this does NOT change auth/network behavior, it only avoids rebuilding
# the same lightweight object graph on every call.
_trade_client_cache: Dict[Tuple[str, str], Any] = {}
_trade_client_cache_lock = threading.Lock()


class _RedactSensitiveWebullFields(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let a logging filter break the request it's logging
            return True
        redacted = _SENSITIVE_LOG_FIELD.sub(r"\1***REDACTED***\2", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _redact_webull_sdk_logging() -> None:
    global _webull_sdk_logging_configured
    if _webull_sdk_logging_configured:
        return
    sdk_logger = logging.getLogger("webull.core")
    sdk_logger.setLevel(logging.INFO)
    handler = logging.FileHandler("webull_trade_sdk.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(thread)d %(asctime)s %(name)s %(levelname)s %(message)s"))
    # The filter goes on the HANDLER, not the logger. The SDK actually logs
    # through child loggers (e.g. "webull.core.http.initializer...") that
    # propagate up to this handler without re-running "webull.core"'s own
    # logger-level filters - only handler-level filters see every record
    # regardless of which descendant logger it originated from.
    handler.addFilter(_RedactSensitiveWebullFields())
    sdk_logger.addHandler(handler)
    _webull_sdk_logging_configured = True


def is_configured(app_key: str, app_secret: str) -> bool:
    return bool((app_key or "").strip() and (app_secret or "").strip())


def _get_trade_client(app_key: str, app_secret: str):
    app_key = (app_key or "").strip()
    app_secret = (app_secret or "").strip()
    if not app_key or not app_secret:
        raise ValueError("Webull API credentials are not configured for this account.")

    cache_key = (app_key, app_secret)
    cached_client = _trade_client_cache.get(cache_key)
    if cached_client is not None:
        return cached_client

    with _trade_client_cache_lock:
        # Re-check inside the lock - another thread may have built and
        # cached this exact credential pair's client while we were waiting.
        cached_client = _trade_client_cache.get(cache_key)
        if cached_client is not None:
            return cached_client

        from webull.core.client import ApiClient
        from webull.trade.trade_client import TradeClient

        _redact_webull_sdk_logging()
        api_client = ApiClient(app_key, app_secret, _REGION_ID)
        # Marks this instance as already having its logging configured, so
        # TradeClient._init_logger skips installing its own (duplicate,
        # unredacted) handler - _redact_webull_sdk_logging above is the only
        # handler that ever gets attached, once per process.
        api_client._stream_logger_set = True
        api_client._file_logger_set = True
        api_client.add_endpoint(_REGION_ID, _SANDBOX_ENDPOINT)
        trade_client = TradeClient(api_client)
        _trade_client_cache[cache_key] = trade_client
        return trade_client


def _call_with_429_retry(action_label: str, call):
    """Retries a read-only Webull API call up to 3 times on a 429
    (rate-limited) response, matching the backoff _place_order_with_retry
    already uses for placement calls. Every read call in this module
    (accounts, balance, positions, open orders, order history) had NO retry
    logic at all until this was added: the SDK raises a raw ServerException
    for ANY non-2xx response (see the note above REPEAT_ORDER_ERROR_CODE
    below), so a single transient 429 previously propagated straight out as
    an unhandled exception - and for the paginated get_open_orders/
    get_order_history specifically, could abort an otherwise-successful
    multi-page read partway through. Re-raises as ValueError, the same
    exception type every existing caller of these getters already handles
    via `except ValueError` - this changes retry behavior only, not what
    callers see on final failure."""
    last_error: Optional[BaseException] = None
    for attempt in range(3):
        try:
            return call()
        except ServerException as error:
            last_error = error
            if error.get_http_status() == 429 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise ValueError(f"Webull API error ({action_label}): HTTP {error.get_http_status()}") from error
        except ClientException as error:
            raise ValueError(f"Webull API error ({action_label}): {error}") from error
    raise ValueError(f"Webull API error ({action_label}): exhausted retries with no response received")


def _extract_orders_page(payload: Any, action_label: str) -> List[Dict[str, Any]]:
    """Shared strict-validation shape check for get_open_orders and
    get_order_history's per-page response - see get_open_orders' own
    docstring for why every one of these checks raises instead of guessing
    (this function feeds a capital calculation that decides how much money
    is safe to commit; a malformed/truncated response silently normalized
    to "zero orders" would UNDER-count committed capital and OVERSTATE
    available buying power, the dangerous direction).

    Accepts TWO real, confirmed response shapes, not one: a JSON object
    with an "orders" key ({"orders": [...]}, previously the only shape
    this accepted), or a bare JSON list. Found live 2026-08-28: a real,
    freshly-connected paper account with zero orders in its history
    returned a bare `[]`, not `{"orders": []}` - the object-wrapper
    shape this function's docstring had previously described as "the one
    real confirmed empty-account shape" turned out not to be the only
    one Webull actually sends. Both shapes carry identical information
    (a list of order rows), so accepting either is not a loosening of
    the fail-closed guarantee above - it's recognizing a second real
    wire format for the same data. The per-row validation below (dict
    shape, stable order_id) is applied identically regardless of which
    shape it came from - a bare list of garbage is rejected exactly as
    strictly as an object-wrapped list of garbage would be."""
    if isinstance(payload, list):
        raw_orders: Any = payload
    elif isinstance(payload, dict):
        if "orders" not in payload:
            raise ValueError(f"Webull API error ({action_label}): response is missing the 'orders' field")
        raw_orders = payload["orders"]
    else:
        raise ValueError(f"Webull API error ({action_label}): expected a JSON object or list response, got {type(payload).__name__}")
    if not isinstance(raw_orders, list):
        raise ValueError(f"Webull API error ({action_label}): 'orders' field is not a list ({type(raw_orders).__name__})")
    if not all(isinstance(order, dict) for order in raw_orders):
        raise ValueError(f"Webull API error ({action_label}): one or more order rows is not a JSON object")
    if not all(order.get("order_id") for order in raw_orders):
        raise ValueError(f"Webull API error ({action_label}): one or more order rows is missing a stable order_id")
    return raw_orders


def get_paper_accounts(app_key: str, app_secret: str) -> List[Dict[str, Any]]:
    trade_client = _get_trade_client(app_key, app_secret)
    response = _call_with_429_retry("accounts", lambda: trade_client.account_v2.get_account_list())
    if response.status_code != 200:
        raise ValueError(f"Webull API error (accounts): HTTP {response.status_code}")
    accounts = response.json()
    return accounts if isinstance(accounts, list) else []


def find_individual_cash_account(accounts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for account in accounts:
        if account.get("account_class") == "INDIVIDUAL_CASH":
            return account
    return accounts[0] if accounts else None


def get_account_balance(app_key: str, app_secret: str, account_id: str) -> Dict[str, Any]:
    trade_client = _get_trade_client(app_key, app_secret)
    response = _call_with_429_retry("balance", lambda: trade_client.account_v2.get_account_balance(account_id))
    if response.status_code != 200:
        raise ValueError(f"Webull API error (balance): HTTP {response.status_code}")
    return response.json()


def get_account_positions(app_key: str, app_secret: str, account_id: str) -> List[Dict[str, Any]]:
    trade_client = _get_trade_client(app_key, app_secret)
    response = _call_with_429_retry("positions", lambda: trade_client.account_v2.get_account_position(account_id))
    if response.status_code != 200:
        raise ValueError(f"Webull API error (positions): HTTP {response.status_code}")
    positions = response.json()
    return positions if isinstance(positions, list) else []


# Webull's client (webull.core.client.Client.get_response) raises a
# ServerException for ANY non-2xx response rather than returning a Response
# object with a checkable .status_code - confirmed by reading the SDK source
# this session. The place_*_order functions used to check
# `response.status_code == 429` after the call to decide whether to retry;
# that branch was unreachable dead code, since a 429 raises before a
# response object is ever returned. _place_order_with_retry (and
# _fetch_order_detail below) replace that pattern with a real try/except
# around the actual exception type.
REPEAT_ORDER_ERROR_CODE = "OAUTH_OPENAPI_TRADE_PLACE_ORDER_REPEAT"


class DefiniteOrderRejection(Exception):
    """Raised when the broker returned a well-formed, PARSED response that
    explicitly rejected the request - e.g. a 400 with a real error_code
    describing what was wrong with the order. Safe to treat as final and
    conclusive: the order definitely never went through (or, from a lookup
    context, the broker definitively has no record of it). Exception class
    alone never proves this - see _classify_server_exception for what
    actually earns this classification vs AmbiguousOrderSubmission."""


class AmbiguousOrderSubmission(Exception):
    """Raised when the broker's true response could not be conclusively
    determined - a network-level failure (timeout, dropped connection), an
    SDK-level exception, a response whose body couldn't be parsed into a
    real error code, or an HTTP status (401/403/429/5xx) that reflects an
    infrastructure/auth/rate-limit failure rather than the broker's trading
    engine having evaluated and rejected THIS specific request. A 5xx in
    particular can occur AFTER a request was already accepted internally,
    before a response made it back - none of these prove rejection. This is
    the fail-safe DEFAULT: anything not explicitly classified as a
    DefiniteOrderRejection raises this instead."""


# HTTP statuses that reflect an infrastructure/auth/rate-limit failure, not
# the broker's trading engine evaluating and rejecting THIS SPECIFIC
# request - see AmbiguousOrderSubmission's docstring for why none of these
# can be treated as proof of rejection.
_AMBIGUOUS_HTTP_STATUSES = {401, 403, 429, 500, 502, 503, 504}

# Error codes EMPIRICALLY CONFIRMED (via live testing against the Webull
# sandbox) to mean the broker parsed the request and explicitly rejected it
# BEFORE any acceptance/execution could occur - genuine, provable evidence
# the order never went through. This starts EMPTY on purpose: no
# rejection-specific error code has actually been observed and confirmed
# live this session. The one code confirmed live so far -
# REPEAT_ORDER_ERROR_CODE (HTTP 417) - means the OPPOSITE (the order
# already exists) and is handled separately above, not here. Do not add a
# code here on assumption, plausibility, or vendor documentation alone -
# only once its exact rejection semantics have been verified against the
# real API, with a comment recording how it was confirmed. An allowlist,
# not a denylist: unknown codes stay ambiguous by default, which is the
# safe direction to be wrong in - the reverse (assuming an unrecognized
# code is a safe rejection) is not.
_CONFIRMED_DEFINITE_REJECTION_ERROR_CODES: frozenset = frozenset()

# Empirically confirmed live 2026-08-28 against the real Webull sandbox: a
# get_order_detail lookup for a client_order_id that was rejected at
# PLACEMENT time (never actually accepted/created - in the specific
# incident that found this, a 417 OPENAPI_ORDER_RISK_RULE_PRICE_AGGRESSIVE
# at placement) comes back HTTP 417, error_code OPENAPI_PARAM_ERR, message
# "Parameter error, Order not present." - an explicit, unambiguous "no
# such order" from the broker's own detail-lookup endpoint, not a vague
# parameter complaint. Deliberately handled ONLY in _fetch_order_detail
# below, NOT added to _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES above
# (which _place_order_with_retry also reads) - OPENAPI_PARAM_ERR is a
# generic "parameter error" code that could mean something else entirely,
# and NOT a safe rejection signal, during order PLACEMENT, where a
# malformed param doesn't prove the order will never exist the way "not
# present" does for a LOOKUP. Matched on the exact message text, not the
# code alone, since the code alone is too generic to trust here - a
# future OPENAPI_PARAM_ERR with a different message must NOT be treated
# as this same "confirmed not present" case. Found live while resolving a
# real frozen admin-panel entry: Release refused with the underlying error
# unsurfaced (see app.py's ambiguous-resolution ValidationError message,
# fixed alongside this to actually show admins the check's real error
# text) until this exact message was captured via a direct API call.
_ORDER_DETAIL_NOT_PRESENT_ERROR_CODE = "OPENAPI_PARAM_ERR"
_ORDER_DETAIL_NOT_PRESENT_MESSAGE_FRAGMENT = "Order not present"


def _classify_server_exception(error: "ServerException", action_label: str) -> Exception:  # noqa: F821 - ServerException imported by callers
    """Turns a raised ServerException into either DefiniteOrderRejection or
    AmbiguousOrderSubmission - never lets a bare ServerException/ValueError
    reach a caller, since exception class alone (the old behavior) proves
    nothing about whether the request actually reached Webull's trading
    engine.

    Classification is allowlist-first, not denylist-first: a code counts as
    a definite, authoritative rejection ONLY if it appears in
    _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES (see that constant for why it
    starts empty) AND its HTTP status isn't itself one of the
    infrastructure/auth/rate-limit statuses in _AMBIGUOUS_HTTP_STATUSES (a
    belt-and-suspenders check - a confirmed rejection code should never
    legitimately arrive with one of those statuses, so seeing both together
    is itself a signal something is off, not a stronger rejection).
    error_code.SDK_UNKNOWN_SERVER_ERROR - the SDK's OWN sentinel for "the
    response body could not be parsed as JSON, or had no error_code field"
    (see webull.core.client.ApiClient._get_server_exception in the vendored
    SDK) - is checked first and is always ambiguous, since there is no
    genuine parsed code to even check against the allowlist. Every other,
    unrecognized code is ambiguous by default."""
    code = error.get_error_code()
    if code == error_code.SDK_UNKNOWN_SERVER_ERROR:
        return AmbiguousOrderSubmission(f"Webull API error ({action_label}): {error}")
    if code in _CONFIRMED_DEFINITE_REJECTION_ERROR_CODES and error.get_http_status() not in _AMBIGUOUS_HTTP_STATUSES:
        return DefiniteOrderRejection(f"Webull API error ({action_label}): {error}")
    return AmbiguousOrderSubmission(f"Webull API error ({action_label}): {error}")


def _place_order_with_retry(trade_client, account_id: str, order: Dict[str, Any], action_label: str) -> Dict[str, Any]:
    """Places one order, retrying on rate-limiting, and treating a reused
    client_order_id as success rather than failure - confirmed live against
    the sandbox this session that Webull rejects a repeated client_order_id
    with HTTP 417 OAUTH_OPENAPI_TRADE_PLACE_ORDER_REPEAT rather than quietly
    creating a duplicate order. That rejection means "this exact order was
    already placed", which is exactly the outcome a caller retrying after a
    crash wants - so it's resolved by looking up and returning the order
    that already exists, not raised as a new failure.

    Every OTHER failure path raises either DefiniteOrderRejection or
    AmbiguousOrderSubmission, never a bare exception - see
    _classify_server_exception. ClientException (a network/SDK-level
    failure - timeout, dropped connection, malformed local request) is
    always ambiguous: it never proves the broker even received the
    request."""
    client_order_id = order["client_order_id"]
    last_error: Optional[ServerException] = None
    for attempt in range(3):
        try:
            response = trade_client.order_v2.place_order(account_id, [order])
            result = response.json()
            result["client_order_id"] = client_order_id
            result["idempotent_replay"] = False
            return result
        except ServerException as error:
            if error.get_http_status() == 417 and error.get_error_code() == REPEAT_ORDER_ERROR_CODE:
                detail = _fetch_order_detail(trade_client, account_id, client_order_id)
                detail["client_order_id"] = client_order_id
                detail["idempotent_replay"] = True
                return detail
            last_error = error
            if error.get_http_status() == 429 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        except ClientException as error:
            raise AmbiguousOrderSubmission(f"Webull API error ({action_label}): {error}") from error
        except Exception as error:  # noqa: BLE001 - anything else (raw network exception, unexpected type) is ambiguous by default, never silently definite
            raise AmbiguousOrderSubmission(f"Webull API error ({action_label}): {error}") from error
    if last_error is None:
        raise AmbiguousOrderSubmission(f"Webull API error ({action_label}): exhausted retries with no response received")
    raise _classify_server_exception(last_error, action_label)


def _fetch_order_detail(trade_client, account_id: str, client_order_id: str) -> Dict[str, Any]:
    """Same classification as _place_order_with_retry - a lookup that
    returns a well-formed, parsed "no such order" style rejection raises
    DefiniteOrderRejection; anything else (network failure, auth/rate-limit/
    server error, unparseable response) raises AmbiguousOrderSubmission.
    Callers reconciling an ambiguous entry (see _reconcile_unknown_submission
    in app.py) rely on this distinction to know whether a failed lookup
    conclusively proves the order was never created, or merely means the
    lookup itself failed for an unrelated reason.

    One case is classified HERE, before falling through to the shared
    _classify_server_exception/_CONFIRMED_DEFINITE_REJECTION_ERROR_CODES
    path - see _ORDER_DETAIL_NOT_PRESENT_ERROR_CODE's own comment for why
    this is deliberately scoped to this lookup-only call site, not the
    shared placement-and-lookup allowlist."""
    for attempt in range(3):
        try:
            response = trade_client.order_v2.get_order_detail(account_id, client_order_id)
        except ServerException as error:
            if error.get_http_status() == 429 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if (
                error.get_error_code() == _ORDER_DETAIL_NOT_PRESENT_ERROR_CODE
                and _ORDER_DETAIL_NOT_PRESENT_MESSAGE_FRAGMENT in (error.get_error_msg() or "")
            ):
                raise DefiniteOrderRejection(f"Webull API error (order detail): {error}") from error
            raise _classify_server_exception(error, "order detail") from error
        except ClientException as error:
            raise AmbiguousOrderSubmission(f"Webull API error (order detail): {error}") from error
        if response.status_code != 200:
            # Reachable only for a 2xx status that isn't literally 200 - a real
            # ServerException (see above) already covers every non-2xx case per
            # the SDK's own get_response() behavior.
            raise AmbiguousOrderSubmission(f"Webull API error (order detail): HTTP {response.status_code}")
        return response.json()
    raise AmbiguousOrderSubmission(f"Webull API error (order detail): exhausted retries with no response received")


def get_order_detail(app_key: str, app_secret: str, account_id: str, client_order_id: str) -> Dict[str, Any]:
    """The current state of one order by its client_order_id - status,
    total_quantity, filled_quantity (see order_lifecycle.summarize_fill).
    Used to confirm an entry's actual fill quantity before sizing protective
    orders, and to confirm a protective order is still genuinely resting
    rather than just "was once accepted"."""
    trade_client = _get_trade_client(app_key, app_secret)
    return _fetch_order_detail(trade_client, account_id, client_order_id)


# get_order_open's own docstring in the SDK says paging is "currently
# supported only for Webull HK" - other regions may just ignore page_size/
# last_order_id/last_client_order_id and always return everything in one
# response. OPEN_ORDERS_PAGE_SIZE and _OPEN_ORDERS_MAX_PAGES exist so this
# still behaves correctly in BOTH cases: if pagination is honored, it walks
# every page; if it's ignored, the very first page already contains
# everything and the "no new order ids seen" check below stops the loop
# after one iteration instead of re-fetching the same full page forever.
OPEN_ORDERS_PAGE_SIZE = 100
_OPEN_ORDERS_MAX_PAGES = 50  # hard ceiling - even a well-behaved paginated account has no business exceeding 5,000 open orders


def get_open_orders(app_key: str, app_secret: str, account_id: str) -> List[Dict[str, Any]]:
    """Every order currently resting at the broker for this account - used
    both to confirm a protective leg is genuinely still active, and to build
    the capital snapshot every autonomous scan sizes candidates against (see
    _build_capital_snapshot in app.py). Paginates until a short page (fewer
    than OPEN_ORDERS_PAGE_SIZE items) signals the end, bounded by
    _OPEN_ORDERS_MAX_PAGES and by cursor-not-advancing detection so an
    account/region that doesn't honor pagination (see the module-level note
    above) can't turn this into an infinite loop re-fetching the same page.

    Every response shape is validated STRICTLY, on purpose - this function's
    caller feeds a capital calculation that decides how much money is safe
    to commit, and _build_capital_snapshot already relies on ANY exception
    here failing the whole snapshot closed (returns None, blocking every
    candidate that scan tick - see its docstring). A malformed response
    silently normalized to "zero open orders" (or a silently TRUNCATED
    result) would instead UNDER-count committed capital and OVERSTATE
    available buying power - the dangerous direction - so this raises
    rather than guesses, in every one of these cases (see
    _extract_orders_page, shared with get_order_history):
      - the payload must be a JSON object with an "orders" key, OR a bare
        JSON list - both are real, confirmed shapes (found live
        2026-08-28: a freshly-connected paper account with zero order
        history returned a bare `[]`, not `{"orders": []}`) - anything
        else (a string, number, null, or a dict missing "orders") raises;
      - "orders" (or the bare list itself) must be a list, and every row
        in it must be a JSON object;
      - every row must carry a stable, truthy order_id - without this, a
        row with a missing/falsy id would collide with every OTHER such
        row when deduplicating by id (None == None), and be silently
        dropped as a "duplicate" rather than counted;
      - a repeated page (the cursor didn't advance - unsupported pagination
        for this region, or a broker bug) raises instead of silently
        stopping with whatever was accumulated so far;
      - exhausting _OPEN_ORDERS_MAX_PAGES without ever reaching a
        legitimate end (a short or empty page) raises instead of silently
        returning a result that may be missing everything past that point."""
    trade_client = _get_trade_client(app_key, app_secret)
    all_orders: List[Dict[str, Any]] = []
    seen_order_ids: set = set()
    last_order_id: Optional[str] = None
    last_client_order_id: Optional[str] = None

    for _ in range(_OPEN_ORDERS_MAX_PAGES):
        response = _call_with_429_retry("open orders", lambda: trade_client.order_v2.get_order_open(
            account_id,
            page_size=OPEN_ORDERS_PAGE_SIZE,
            last_order_id=last_order_id,
            last_client_order_id=last_client_order_id,
        ))
        if response.status_code != 200:
            raise ValueError(f"Webull API error (open orders): HTTP {response.status_code}")
        page_orders = _extract_orders_page(response.json(), "open orders")
        if not page_orders:
            break

        new_orders = [order for order in page_orders if order["order_id"] not in seen_order_ids]
        if not new_orders:
            # The cursor didn't advance - the same page came back again
            # (unsupported pagination for this region, or a broker bug).
            # Raising here (not silently stopping) matters: a caller must
            # never trust the partial result as complete.
            raise ValueError(
                "Webull API error (open orders): the same page was returned again (cursor did not advance) - "
                "cannot safely continue without risking a truncated (incomplete) order list"
            )
        all_orders.extend(new_orders)
        seen_order_ids.update(order["order_id"] for order in new_orders)

        if len(page_orders) < OPEN_ORDERS_PAGE_SIZE:
            break  # short page - this was the last one

        last_order_id = page_orders[-1]["order_id"]
        last_client_order_id = page_orders[-1].get("client_order_id") or last_client_order_id
    else:
        # The loop ran _OPEN_ORDERS_MAX_PAGES times without ever hitting a
        # legitimate "done" break (a short or empty page) - there may be
        # MORE data past this point. Returning what was accumulated so far
        # would be exactly the silent-truncation risk this function exists
        # to avoid.
        raise ValueError(
            f"Webull API error (open orders): exhausted the {_OPEN_ORDERS_MAX_PAGES}-page limit without reaching "
            "the end of the account's open orders - the result would be incomplete"
        )

    return all_orders


ORDER_HISTORY_LOOKBACK_DAYS = 7
ORDER_HISTORY_PAGE_SIZE = 100


_ORDER_HISTORY_MAX_PAGES = 50  # same reasoning as _OPEN_ORDERS_MAX_PAGES - no genuine account/window has any business exceeding 5,000 historical orders


def get_order_history(app_key: str, app_secret: str, account_id: str, days_back: int = ORDER_HISTORY_LOOKBACK_DAYS) -> List[Dict[str, Any]]:
    """Historical (filled/cancelled/rejected) orders over the last
    `days_back` days - the SDK's own docstring confirms pagination
    (last_order_id/last_client_order_id) is supported for Webull US on this
    endpoint (unlike get_order_open's pagination, which is HK-only), so it's
    usable for this sandbox's region.

    Exists specifically as ONE of several independent evidence sources when
    manually resolving an entry stuck in UNKNOWN_SUBMISSION_STATE (see
    _gather_ambiguous_submission_evidence in app.py): get_open_orders only
    shows currently-RESTING orders, so an order that already filled, was
    cancelled, or was rejected days ago has already fallen out of that view
    - this is what still has a record of it.

    A manual "release" decision relies on this coming back COMPLETE for the
    requested window - a silently truncated single page (an account with
    more than ORDER_HISTORY_PAGE_SIZE historical orders in the window) would
    read as "nothing found here" exactly as easily as a genuinely clean
    history would, which is the wrong-direction failure for a check that
    exists to justify releasing a capital freeze. So this now applies the
    SAME fail-closed pagination hardening as get_open_orders, for the same
    reasons - see that function's docstring for the exact list of conditions
    that raise instead of guessing:
      - the payload itself must be a JSON object;
      - it must contain an "orders" key (missing key != empty list);
      - "orders" must be a list of JSON objects;
      - every row must carry a stable, truthy order_id;
      - a repeated page (cursor did not advance) raises rather than
        silently stopping with a partial result;
      - exhausting _ORDER_HISTORY_MAX_PAGES without reaching a legitimate
        end (a short or empty page) raises rather than returning whatever
        was accumulated so far.
    A raised exception here is still treated by the evidence-gathering step
    as "this particular check is inconclusive" - the same fail-safe outcome
    as any other check failing - so callers do not need their own separate
    handling for the truncation case; it surfaces as exactly the same
    "errors" entry any other broker-side failure would."""
    trade_client = _get_trade_client(app_key, app_secret)
    end_date = _today_utc_isoformat()
    start_date = _days_before_utc_isoformat(days_back)
    all_orders: List[Dict[str, Any]] = []
    seen_order_ids: set = set()
    last_order_id: Optional[str] = None
    last_client_order_id: Optional[str] = None

    for _ in range(_ORDER_HISTORY_MAX_PAGES):
        response = _call_with_429_retry("order history", lambda: trade_client.order_v2.get_order_history(
            account_id,
            page_size=ORDER_HISTORY_PAGE_SIZE,
            start_date=start_date,
            end_date=end_date,
            last_order_id=last_order_id,
            last_client_order_id=last_client_order_id,
        ))
        if response.status_code != 200:
            raise ValueError(f"Webull API error (order history): HTTP {response.status_code}")
        page_orders = _extract_orders_page(response.json(), "order history")
        if not page_orders:
            break

        new_orders = [order for order in page_orders if order["order_id"] not in seen_order_ids]
        if not new_orders:
            raise ValueError(
                "Webull API error (order history): the same page was returned again (cursor did not advance) - "
                "cannot safely continue without risking a truncated (incomplete) order history"
            )
        all_orders.extend(new_orders)
        seen_order_ids.update(order["order_id"] for order in new_orders)

        if len(page_orders) < ORDER_HISTORY_PAGE_SIZE:
            break  # short page - this was the last one

        last_order_id = page_orders[-1]["order_id"]
        last_client_order_id = page_orders[-1].get("client_order_id") or last_client_order_id
    else:
        raise ValueError(
            f"Webull API error (order history): exhausted the {_ORDER_HISTORY_MAX_PAGES}-page limit without reaching "
            "the end of the account's order history for this window - the result would be incomplete"
        )

    return all_orders


def _today_utc_isoformat() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


def _days_before_utc_isoformat(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()


def preview_stock_order(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    side: str,
    quantity: float,
    limit_price: float,
    trading_session: str = "CORE",
) -> Dict[str, Any]:
    trade_client = _get_trade_client(app_key, app_secret)
    order = {
        "combo_type": "NORMAL",
        "client_order_id": uuid.uuid4().hex,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "LIMIT",
        "limit_price": str(limit_price),
        "quantity": str(quantity),
        "support_trading_session": trading_session,
        "side": side,
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    response = trade_client.order_v2.preview_order(account_id, [order])
    if response.status_code != 200:
        raise ValueError(f"Webull API error (preview order): HTTP {response.status_code} {response.text}")
    return response.json()


def place_stock_order(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    side: str,
    quantity: float,
    limit_price: float,
    trading_session: str = "CORE",
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Places a real (sandbox) DAY limit order. trading_session must be CORE
    (regular hours), ALL (extended hours), or NIGHT (Webull's 24-hour session -
    the only one accepted outside pre-market/after-hours windows, and it draws
    from a separate night_trading_buying_power pool rather than the account's
    regular buying power).

    client_order_id should be a deterministic id (see
    order_lifecycle.deterministic_client_order_id) for any caller that might
    retry this same logical placement after a crash - a fresh random id is
    generated only if none is supplied, which loses Webull's own
    duplicate-order protection."""
    trade_client = _get_trade_client(app_key, app_secret)
    client_order_id = client_order_id or uuid.uuid4().hex
    order = {
        "combo_type": "NORMAL",
        "client_order_id": client_order_id,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "LIMIT",
        "limit_price": str(limit_price),
        "quantity": str(quantity),
        "support_trading_session": trading_session,
        "side": side,
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    return _place_order_with_retry(trade_client, account_id, order, "place order")


def place_stop_loss_order(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    quantity: float,
    stop_price: float,
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Places a real standalone stop-loss SELL order at the broker - once this
    is accepted, Webull itself watches the price and executes the exit, with
    no dependency on this app polling or a cron job firing in time. order_type
    must be the literal string "STOP_LOSS" (underscore, not a space - "STOP
    LOSS" is rejected with OAUTH_OPENAPI_PARAM_ERR, verified via preview_order
    before this was wired into live placement). support_trading_session is
    hardcoded to "CORE" - unlike LIMIT orders, STOP_LOSS rejects both "NIGHT"
    and "ALL" as invalid parameter values (confirmed live against the sandbox
    API), so stop-loss orders can only be placed while the core trading
    gateway is up (roughly 9:30-16:00 ET). Callers placing an entry outside
    those hours should expect this to fail and retry once CORE hours begin -
    see _reconcile_exit_orders in app.py.

    client_order_id: see place_stock_order."""
    trade_client = _get_trade_client(app_key, app_secret)
    client_order_id = client_order_id or uuid.uuid4().hex
    order = {
        "combo_type": "NORMAL",
        "client_order_id": client_order_id,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "STOP_LOSS",
        "stop_price": str(stop_price),
        "quantity": str(quantity),
        "support_trading_session": "CORE",
        "side": "SELL",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    return _place_order_with_retry(trade_client, account_id, order, "place stop-loss order")


def place_take_profit_order(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    quantity: float,
    target_price: float,
    trading_session: str = "CORE",
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Places a real take-profit SELL order at the broker - a plain LIMIT
    order resting above the current price, which Webull fills automatically
    once the market reaches it, same as any other limit order. Unlike
    STOP_LOSS, LIMIT accepts CORE/ALL/NIGHT normally (confirmed earlier
    against the sandbox API), so this has no trading-hours restriction. This
    order rides alongside the STOP_LOSS order placed at entry as an
    independent bracket rather than a true OTOCO combo - see the
    _reconcile_exit_orders note in app.py for how the stale-leg risk that
    creates is handled.

    client_order_id: see place_stock_order."""
    trade_client = _get_trade_client(app_key, app_secret)
    client_order_id = client_order_id or uuid.uuid4().hex
    order = {
        "combo_type": "NORMAL",
        "client_order_id": client_order_id,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "LIMIT",
        "limit_price": str(target_price),
        "quantity": str(quantity),
        "support_trading_session": trading_session,
        "side": "SELL",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    return _place_order_with_retry(trade_client, account_id, order, "place take-profit order")


# --- TEMPORARY diagnostic: verify a real STOP_LOSS_PROFIT combo order (2026-08-31) -----
# Found live: place_stop_loss_order and place_take_profit_order are placed as
# two fully INDEPENDENT orders (see place_take_profit_order's own docstring -
# a deliberate choice, since Webull's combo-order wire format has never been
# empirically verified against this sandbox account). A real SLB entry hit
# the cost of that gap today: after the stop leg placed and rested, the
# take-profit leg was REJECTED outright - HTTP 417,
# OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION, "this order cannot be entered
# because it will reverse an existing position" - because Webull's risk
# engine reads two independent full-quantity SELL orders against one long
# position as an attempt to go net short. preview_order is a genuine dry-run
# (validates the request shape and rejects/accepts exactly like a real
# placement would, but never actually executes an order or touches capital -
# same trust level as the STOP_LOSS order_type quirk that was verified this
# same way before being wired into live placement) - safe to call against
# the real, currently-stuck SLB position without risking it further.
# Remove once STOP_LOSS_PROFIT combo orders are either adopted for real or
# ruled out.
def preview_stop_and_target_combo(
    app_key: str,
    app_secret: str,
    account_id: str,
    symbol: str,
    quantity: float,
    stop_price: float,
    target_price: float,
) -> Dict[str, Any]:
    trade_client = _get_trade_client(app_key, app_secret)
    combo_order_id = uuid.uuid4().hex
    stop_leg = {
        "combo_type": "STOP_LOSS_PROFIT",
        "client_order_id": uuid.uuid4().hex,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "STOP_LOSS",
        "stop_price": str(stop_price),
        "quantity": str(quantity),
        "support_trading_session": "CORE",
        "side": "SELL",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    target_leg = {
        "combo_type": "STOP_LOSS_PROFIT",
        "client_order_id": uuid.uuid4().hex,
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": "LIMIT",
        "limit_price": str(target_price),
        "quantity": str(quantity),
        "support_trading_session": "CORE",
        "side": "SELL",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
    }
    response = trade_client.order_v2.preview_order(account_id, [stop_leg, target_leg], client_combo_order_id=combo_order_id)
    return response.json()
# --- end temporary STOP_LOSS_PROFIT combo diagnostic ------------------------


def cancel_order(app_key: str, app_secret: str, account_id: str, client_order_id: str) -> Dict[str, Any]:
    trade_client = _get_trade_client(app_key, app_secret)
    response = trade_client.order_v2.cancel_order(account_id, client_order_id)
    if response.status_code != 200:
        raise ValueError(f"Webull API error (cancel order): HTTP {response.status_code} {response.text}")
    return response.json()
