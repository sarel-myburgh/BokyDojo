"""Rate limiting and progressive lockout — TODO 0.6.5, SEC 2.1."""

from __future__ import annotations

import pytest
from django.core.cache import cache

from apps.core import throttle
from apps.core.throttle import (
    LOGIN_POLICY,
    PIN_POLICY,
    LockoutPolicy,
    Throttled,
    ThrottleState,
    enforce,
    peek,
    register_failure,
    register_success,
    reset,
)

FAST = LockoutPolicy(max_attempts=3, window_seconds=60, escalation=(10, 60, 300))


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def _fail(times: int, scope="login", who="a@example.com", policy=FAST) -> ThrottleState:
    state = None
    for _ in range(times):
        state = register_failure(scope, who, policy)
    return state


def test_clean_identifier_is_allowed():
    assert peek("login", "new@example.com", FAST).allowed


def test_failures_below_threshold_do_not_lock():
    state = _fail(2)
    assert state.locked is False
    assert state.failures == 2


def test_threshold_triggers_lockout():
    state = _fail(3)
    assert state.locked is True
    assert state.retry_after == 10


def test_enforce_raises_once_locked():
    _fail(3)
    with pytest.raises(Throttled) as excinfo:
        enforce("login", "a@example.com", FAST)
    assert excinfo.value.state.retry_after > 0


def test_enforce_passes_when_clean():
    assert enforce("login", "clean@example.com", FAST).allowed


def test_lockout_escalates_with_each_burst():
    """A fixed lockout is either too short to deter or too harsh on a typo.
    Each further burst must cost more."""
    first = _fail(3)
    assert first.retry_after == 10

    cache.delete(throttle._keys("login", "a@example.com")[2])  # simulate expiry
    second = _fail(3)
    assert second.retry_after == 60

    cache.delete(throttle._keys("login", "a@example.com")[2])
    third = _fail(3)
    assert third.retry_after == 300


def test_escalation_repeats_the_final_tier():
    for _ in range(5):
        _fail(3)
        cache.delete(throttle._keys("login", "a@example.com")[2])
    assert _fail(3).retry_after == 300


def test_success_clears_counters_and_tier():
    """Someone who eventually signs in should not carry a hair-trigger lockout
    around for the rest of the day."""
    _fail(3)
    register_success("login", "a@example.com")

    assert peek("login", "a@example.com", FAST).allowed
    assert _fail(3).retry_after == 10  # back to the first tier


def test_admin_reset_unlocks():
    _fail(3)
    reset("login", "a@example.com")
    assert peek("login", "a@example.com", FAST).allowed


# -- isolation ----------------------------------------------------------------


def test_scopes_are_independent():
    """A kiosk PIN lockout must not lock the same person out of the portal."""
    _fail(3, scope="pin", who="student-1", policy=FAST)
    assert peek("pin", "student-1", FAST).locked is True
    assert peek("login", "student-1", FAST).locked is False


def test_identifiers_are_independent():
    _fail(3, who="victim@example.com")
    assert peek("login", "other@example.com", FAST).allowed


def test_identifier_is_not_stored_in_plain_cache_keys():
    """Cache dumps and log lines should not leak the email being attacked."""
    _fail(1, who="secret@example.com")
    assert all("secret@example.com" not in key for key in throttle._keys("login", "secret@example.com"))


# -- shipped policies ---------------------------------------------------------


def test_pin_policy_is_stricter_than_login():
    """A 4-6 digit PIN entered in public is cheap to brute force, so it must
    lock sooner than a password."""
    assert PIN_POLICY.max_attempts < LOGIN_POLICY.max_attempts


def test_every_policy_escalates():
    from apps.core.throttle import API_POLICY, RESET_POLICY

    for policy in (LOGIN_POLICY, PIN_POLICY, RESET_POLICY, API_POLICY):
        durations = [policy.duration_for_tier(t) for t in range(1, len(policy.escalation) + 1)]
        assert durations == sorted(durations)
        assert durations[0] > 0


def test_tier_zero_has_no_duration():
    assert FAST.duration_for_tier(0) == 0
