from __future__ import annotations

import json

import pytest

from autonomy import autonomous_controller as ac

"""Section 5 of the memory/real-outcomes/multi-source-data plan (2026-09-11):
infrastructure only - a per-account validated_brains list, defaulting to
empty, gating whether a currently shadow-only brain (candle_brain,
pattern_brain, neural_engine - see ac.SHADOW_ONLY_BRAINS) could ever be
read into live scoring for that account. Nothing in this codebase adds a
name to this list automatically - see set_validated_brains' own docstring
for why. These tests prove the gate itself works correctly; they do not
(and, as of this writing, cannot) prove anything about a live call site
reading it, because no such call site exists yet - see
docs/AGENT_ARCHITECTURE.md and _default_settings' own comment for why."""


def test_validated_brains_defaults_to_empty_for_a_brand_new_account(user_id):
    status = ac.get_autonomy_status(user_id)
    assert status["validated_brains"] == []


@pytest.mark.parametrize("brain_name", ac.SHADOW_ONLY_BRAINS)
def test_every_shadow_only_brain_is_unvalidated_by_default(user_id, brain_name):
    assert ac.is_brain_validated(user_id, brain_name) is False


def test_setting_a_known_shadow_brain_persists_and_is_reflected_by_the_gate(user_id):
    result = ac.set_validated_brains(user_id, ["candle_brain"])
    assert result["validated_brains"] == ["candle_brain"]
    assert ac.is_brain_validated(user_id, "candle_brain") is True
    # Only the named brain is validated - not every shadow-only brain.
    assert ac.is_brain_validated(user_id, "pattern_brain") is False

    # Round-trips through a fresh read, not just the mutator's own return value.
    status = ac.get_autonomy_status(user_id)
    assert status["validated_brains"] == ["candle_brain"]


def test_setting_an_unknown_brain_name_is_rejected(user_id):
    with pytest.raises(ValueError):
        ac.set_validated_brains(user_id, ["not_a_real_brain"])
    # Rejected - must not have partially applied.
    assert ac.get_autonomy_status(user_id)["validated_brains"] == []


def test_setting_a_brain_that_already_feeds_live_scoring_is_rejected():
    # strategy_brain/charting_brain/etc. already feed live scoring
    # unconditionally (see docs/AGENT_ARCHITECTURE.md) - this gate exists
    # only for the currently-silent brains, not as a generic allowlist for
    # anything already live. Rejecting a non-shadow name here prevents
    # this field from ever looking like it controls something it doesn't.
    with pytest.raises(ValueError):
        ac.set_validated_brains("some-user", ["strategy_brain"])


def test_validated_brains_deduplicates_and_sorts(user_id):
    result = ac.set_validated_brains(user_id, ["neural_engine", "candle_brain", "candle_brain"])
    assert result["validated_brains"] == ["candle_brain", "neural_engine"]


def test_setting_an_empty_list_clears_previously_validated_brains(user_id):
    ac.set_validated_brains(user_id, ["candle_brain"])
    result = ac.set_validated_brains(user_id, [])
    assert result["validated_brains"] == []
    assert ac.is_brain_validated(user_id, "candle_brain") is False


def test_the_gate_is_per_account_not_global(user_id, other_user_id):
    ac.set_validated_brains(user_id, ["candle_brain"])
    assert ac.is_brain_validated(user_id, "candle_brain") is True
    assert ac.is_brain_validated(other_user_id, "candle_brain") is False


def test_the_gate_defensively_refuses_a_non_shadow_brain_even_if_forced_onto_disk(user_id):
    # Belt-and-suspenders: even if validated_brains somehow contained a
    # name outside SHADOW_ONLY_BRAINS (e.g. hand-edited on disk, or a
    # future bug in the setter's own validation), is_brain_validated must
    # still refuse it - this gate must never become a backdoor around a
    # brain it was never designed to cover.
    path = ac._settings_file(user_id)
    ac.get_autonomy_status(user_id)  # ensure the file exists first
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    on_disk["validated_brains"] = ["strategy_brain"]
    path.write_text(json.dumps(on_disk), encoding="utf-8")

    assert ac.is_brain_validated(user_id, "strategy_brain") is False
