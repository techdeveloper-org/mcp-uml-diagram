"""Token bucket rate limiter for MCP server tool calls.

Applied per-client (by IP or process identity) to prevent abuse.
All limiting is optional: enabled only when ENABLE_RATE_LIMITING=1.

Usage:
    from rate_limiter import check_rate_limit
    result = check_rate_limit(client_id="default", bucket="tool_calls")
    if not result["allowed"]:
        return result  # {"allowed": False, "error": "rate_limit_exceeded", "retry_after": N}

Windows-Safe: ASCII only (cp1252 compatible)
"""

import math
import os
import threading
import time

# Module-level bucket registry: keyed by (client_id, bucket_name)
_buckets = {}  # type: dict
_buckets_lock = threading.Lock()

# Default bucket configurations: (capacity, refill_rate tokens/second)
_BUCKET_DEFAULTS = {
    "tool_calls": (100, 100.0 / 60.0),  # 100 per minute
    "llm_calls": (10, 10.0 / 60.0),  # 10 per minute
    # For tools that fan out internally: one invocation issues several upstream
    # requests, so the tool-call rate is not the request rate. A tool looping
    # ten calls per invocation at the tool_calls rate would issue 1000 upstream
    # requests a minute. Sized so that ten-fold amplification stays within a
    # 600/minute upstream cap, which is the Google URL Inspection limit that
    # motivated it.
    "amplified_calls": (10, 10.0 / 60.0),  # 10 per minute
}

_FALLBACK_BUCKET = (60, 60.0 / 60.0)

# Fallback advice when a bucket has a non-positive refill rate and can
# therefore never recover on its own.
_RETRY_AFTER_SECONDS = 60

# Registry size above which _get_or_create_bucket prunes provably-full buckets.
# client_id is caller-supplied (an IP or process identity), so the key space is
# unbounded; without pruning the registry grows for the process lifetime.
_PRUNE_THRESHOLD = 1024


class TokenBucket(object):
    """Thread-safe token bucket for rate limiting.

    Tokens are refilled continuously based on elapsed monotonic time since
    the last refill check. The bucket never exceeds its capacity.

    A monotonic clock is used rather than wall-clock time: a backward wall-clock
    step (NTP correction, manual change) would produce a negative elapsed
    interval and subtract tokens, locking a client out until the clock caught
    back up, while a forward step would refill the bucket instantly.

    Args:
        capacity: Maximum number of tokens the bucket can hold.
        refill_rate: Tokens added per second (can be fractional).
    """

    def __init__(self, capacity, refill_rate):
        # type: (float, float) -> None
        self._capacity = float(capacity)
        self._refill_rate = float(refill_rate)
        self._tokens = float(capacity)  # start full
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        # type: () -> None
        """Add tokens based on elapsed time. Must be called under self._lock."""
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_refill)
        added = elapsed * self._refill_rate
        self._tokens = min(self._capacity, self._tokens + added)
        self._last_refill = now

    def consume(self, tokens=1):
        # type: (int) -> bool
        """Attempt to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume (default 1).

        Returns:
            True if tokens were available and consumed, False if the bucket
            did not have enough tokens.
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def seconds_until_available(self, tokens=1):
        # type: (int) -> int
        """Seconds until the bucket holds at least the requested tokens.

        Args:
            tokens: Number of tokens the caller needs.

        Returns:
            0 if the tokens are already available, otherwise the number of
            whole seconds (rounded up) until they will be. Buckets with a
            non-positive refill rate never recover and report the fallback
            retry window instead.
        """
        with self._lock:
            self._refill()
            deficit = tokens - self._tokens
            if deficit <= 0:
                return 0
            if self._refill_rate <= 0:
                return _RETRY_AFTER_SECONDS
            return int(math.ceil(deficit / self._refill_rate))

    def is_provably_full(self):
        # type: () -> bool
        """Whether the bucket has been idle long enough to be certainly full.

        A bucket idle for at least capacity/refill_rate seconds has refilled
        from empty to capacity, so discarding it and recreating it on the next
        request (which starts full) is observationally equivalent. This is what
        makes registry pruning safe rather than a way to bypass the limit.

        Returns:
            True when the bucket can be discarded without weakening the limit.
        """
        with self._lock:
            if self._refill_rate <= 0:
                return False
            idle = max(0.0, time.monotonic() - self._last_refill)
            return idle >= (self._capacity / self._refill_rate)


def _prune_full_buckets():
    # type: () -> None
    """Drop provably-full buckets from the registry.

    Must be called while holding _buckets_lock. Lock ordering is
    _buckets_lock then TokenBucket._lock; no code path takes them in the
    opposite order, so this cannot deadlock.
    """
    stale = [key for key, bucket in _buckets.items() if bucket.is_provably_full()]
    for key in stale:
        del _buckets[key]


def _get_or_create_bucket(client_id, bucket_name):
    # type: (str, str) -> TokenBucket
    """Return existing bucket or create a new one with default settings."""
    key = (client_id, bucket_name)
    with _buckets_lock:
        bucket = _buckets.get(key)
        if bucket is not None:
            return bucket

        if len(_buckets) >= _PRUNE_THRESHOLD:
            _prune_full_buckets()

        capacity, refill_rate = _BUCKET_DEFAULTS.get(bucket_name, _FALLBACK_BUCKET)
        bucket = TokenBucket(capacity, refill_rate)
        _buckets[key] = bucket
        return bucket


def check_rate_limit(client_id="default", bucket="tool_calls"):
    # type: (str, str) -> dict
    """Check whether a client is within its rate limit for a given bucket.

    If ENABLE_RATE_LIMITING is not set to "1" this function always returns
    allowed without creating any bucket state.

    Args:
        client_id: Identifier for the client (e.g. IP address, process ID,
                   or "default" for a shared anonymous bucket).
        bucket: Name of the rate limit bucket. Predefined buckets:
                "tool_calls" (100/min) for ordinary tools, "llm_calls"
                (10/min) for calls that bill or consume inference per
                invocation, and "amplified_calls" (10/min) for tools that
                issue several upstream requests per invocation, where the
                tool-call rate is not the upstream request rate.
                Unknown bucket names fall back to 60/min defaults.

    Returns:
        dict with key "allowed" (bool).
        On denial also includes "error" ("rate_limit_exceeded") and
        "retry_after" (int seconds until the next token is actually available,
        derived from the bucket's own refill rate).
    """
    if os.environ.get("ENABLE_RATE_LIMITING") != "1":
        return {"allowed": True}

    token_bucket = _get_or_create_bucket(client_id, bucket)
    if token_bucket.consume():
        return {"allowed": True}

    return {
        "allowed": False,
        "error": "rate_limit_exceeded",
        "retry_after": token_bucket.seconds_until_available(),
    }
