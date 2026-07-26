"""Rate limiting and progressive lockout — TODO 0.6.5 / SEC 2.1.

Guards login, password reset, kiosk PIN entry and the API. Per SEC §1.2 the
most likely attacker here is an opportunistic scanner, and the second most
likely is an authenticated parent poking at things — both are defeated cheaply
by making repeated attempts expensive.

Lockout is **progressive**: each further burst of failures locks for longer.
A fixed lockout is either too short to deter a patient attacker or long enough
that anyone who fat-fingers their password twice is locked out of their own
invoice. Escalation gives both.

Counters are keyed on *scope + identifier*, never on identifier alone, so a
lockout on kiosk PIN entry cannot lock someone out of the parent portal.

⚠ The cache is the store. On a single-container deployment that is fine. If the
app is ever run multi-process without a shared cache, lockouts become
per-process and the protection weakens — use Redis, not LocMemCache.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.core.cache import cache

CACHE_PREFIX = "throttle"


@dataclass(frozen=True)
class LockoutPolicy:
    """How many failures are tolerated, and how long each lockout tier lasts."""

    #: Failures allowed within the window before the first lockout.
    max_attempts: int
    #: How long failures are remembered, in seconds.
    window_seconds: int
    #: Lockout duration per tier, in seconds. The last value repeats.
    escalation: tuple[int, ...]

    def duration_for_tier(self, tier: int) -> int:
        if tier <= 0:
            return 0
        index = min(tier - 1, len(self.escalation) - 1)
        return self.escalation[index]


#: Interactive sign-in. Generous enough for a mistyped password, harsh quickly
#: after that.
LOGIN_POLICY = LockoutPolicy(
    max_attempts=5,
    window_seconds=15 * 60,
    escalation=(60, 5 * 60, 30 * 60, 60 * 60),
)

#: Kiosk PIN. A PIN is 4-6 digits and is entered in public, so brute force is
#: cheap for an attacker — lock harder and sooner. It is a convenience control,
#: never a security boundary (SEC §2.7).
PIN_POLICY = LockoutPolicy(
    max_attempts=3,
    window_seconds=10 * 60,
    escalation=(60, 10 * 60, 60 * 60),
)

#: Password reset. Throttled to blunt user enumeration and mail flooding.
RESET_POLICY = LockoutPolicy(
    max_attempts=3,
    window_seconds=60 * 60,
    escalation=(5 * 60, 30 * 60, 2 * 60 * 60),
)

API_POLICY = LockoutPolicy(
    max_attempts=10,
    window_seconds=60,
    escalation=(60, 5 * 60),
)


@dataclass(frozen=True)
class ThrottleState:
    locked: bool
    failures: int
    tier: int
    retry_after: int

    @property
    def allowed(self) -> bool:
        return not self.locked


class Throttled(Exception):
    """Raised by ``enforce()`` when the caller must wait."""

    def __init__(self, state: ThrottleState):
        self.state = state
        super().__init__(f"Locked out; retry in {state.retry_after}s")


def _hash(identifier: str) -> str:
    """Identifiers are emails, usernames and IPs — do not store them in plain
    cache keys that might be dumped or logged."""
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]


def _keys(scope: str, identifier: str) -> tuple[str, str, str]:
    digest = _hash(identifier)
    base = f"{CACHE_PREFIX}:{scope}:{digest}"
    return f"{base}:failures", f"{base}:tier", f"{base}:until"


def _now() -> float:
    import time

    return time.monotonic()


def peek(scope: str, identifier: str, policy: LockoutPolicy) -> ThrottleState:
    """Current state without recording anything."""
    failures_key, tier_key, until_key = _keys(scope, identifier)
    failures = cache.get(failures_key, 0)
    tier = cache.get(tier_key, 0)
    locked_until = cache.get(until_key)

    if locked_until is None:
        return ThrottleState(locked=False, failures=failures, tier=tier, retry_after=0)

    remaining = int(locked_until - _now())
    if remaining <= 0:
        cache.delete(until_key)
        return ThrottleState(locked=False, failures=failures, tier=tier, retry_after=0)

    return ThrottleState(locked=True, failures=failures, tier=tier, retry_after=remaining)


def enforce(scope: str, identifier: str, policy: LockoutPolicy) -> ThrottleState:
    """Raise ``Throttled`` if locked out. Call *before* checking a credential."""
    state = peek(scope, identifier, policy)
    if state.locked:
        raise Throttled(state)
    return state


def register_failure(scope: str, identifier: str, policy: LockoutPolicy) -> ThrottleState:
    """Record a failed attempt and lock out if the threshold is crossed."""
    failures_key, tier_key, until_key = _keys(scope, identifier)

    failures = cache.get(failures_key, 0) + 1
    cache.set(failures_key, failures, policy.window_seconds)

    if failures < policy.max_attempts:
        return ThrottleState(
            locked=False,
            failures=failures,
            tier=cache.get(tier_key, 0),
            retry_after=0,
        )

    tier = cache.get(tier_key, 0) + 1
    duration = policy.duration_for_tier(tier)

    # Remember the tier well beyond the lockout so a patient attacker who waits
    # out one lockout does not get the short first tier again.
    cache.set(tier_key, tier, max(policy.window_seconds, duration) * 4)
    cache.set(until_key, _now() + duration, duration)
    cache.set(failures_key, 0, policy.window_seconds)

    return ThrottleState(locked=True, failures=failures, tier=tier, retry_after=duration)


def register_success(scope: str, identifier: str) -> None:
    """Clear counters after a genuine success.

    The escalation tier is cleared too: a legitimate user who eventually signs
    in should not carry a hair-trigger lockout around for the rest of the day.
    """
    for key in _keys(scope, identifier):
        cache.delete(key)


def reset(scope: str, identifier: str) -> None:
    """Administrative unlock — e.g. a dojo admin clearing a student's PIN lockout."""
    register_success(scope, identifier)
