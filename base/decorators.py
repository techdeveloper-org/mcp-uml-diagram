"""
MCP Tool Decorators - Cross-cutting concerns for all 109 MCP tools.

Design Pattern: Decorator (structural)
  - Wraps tool functions with standardized error handling
  - Eliminates 109 identical try/except blocks across 11 servers
  - Adds optional logging, timing, and response normalization
  - Includes ``error_type`` in error responses for programmatic handling

Before (repeated 109 times)::

    @mcp.tool()
    def my_tool(arg: str) -> str:
        try:
            result = do_work(arg)
            return _json({"success": True, "result": result})
        except SomeError as e:
            return _json({"success": False, "error": str(e)})

After::

    @mcp.tool()
    @mcp_tool_handler
    def my_tool(arg: str) -> dict:
        result = do_work(arg)
        return {"result": result}  # auto-wrapped with success/error

Windows-Safe: ASCII only (cp1252 compatible)
"""

import functools
import os
import sys
import threading
import time
import traceback
from typing import Callable, Optional, Tuple, Type, Union

from .response import _serialize

# ``rate_limiter`` lives at the repository root while this module lives inside
# the package, in both the mcp-base layout and the vendored ``base/`` layout.
# Any server that can import this module already has that root on sys.path,
# so a flat import resolves in both. It is guarded anyway: a server that
# vendors the package without the limiter must keep working.
try:
    from rate_limiter import check_rate_limit as _check_rate_limit
    _RATE_LIMITER_AVAILABLE = True
except ImportError:
    _RATE_LIMITER_AVAILABLE = False

    def _check_rate_limit(client_id="default", bucket="tool_calls"):
        """Fallback used when the limiter module is not importable."""
        return {"allowed": True}


_MISSING_LIMITER_WARNED = threading.Event()


def _log_warning(event: str, **fields) -> None:
    """Emit a single-line structured warning to stderr.

    Defined locally so this module stays self-contained when vendored as
    ``base/decorators.py``. Output is forced to ASCII because stderr on
    Windows is cp1252.

    Args:
        event: Short machine-readable event name.
        **fields: Additional key=value context.
    """
    parts = ["level=WARNING", "component=mcp_base.decorators", "event=" + event]
    parts.extend("{}={}".format(key, value) for key, value in fields.items())
    line = " ".join(parts)
    try:
        sys.stderr.write(line.encode("ascii", "backslashreplace").decode("ascii") + "\n")
    except (OSError, ValueError):
        pass


def _rate_limit_verdict(bucket: str) -> dict:
    """Consume one token from ``bucket`` and report whether the call may run.

    Enforcement is opt-in: with ENABLE_RATE_LIMITING unset, the limiter
    returns allowed without creating any bucket state, so this costs one
    environment lookup and nothing else.

    If limiting is switched on but the limiter module could not be imported,
    this warns once rather than failing open silently -- an operator who set
    the variable is entitled to know it is doing nothing.

    Args:
        bucket: Name of the token bucket to draw from.

    Returns:
        The limiter verdict dict, always containing "allowed".
    """
    if not _RATE_LIMITER_AVAILABLE:
        if (os.environ.get("ENABLE_RATE_LIMITING") == "1"
                and not _MISSING_LIMITER_WARNED.is_set()):
            _MISSING_LIMITER_WARNED.set()
            _log_warning(
                "rate_limiting_enabled_but_limiter_unavailable",
                detail="ENABLE_RATE_LIMITING=1 has no effect; rate_limiter is not importable",
            )
        return {"allowed": True}
    return _check_rate_limit(bucket=bucket)


def mcp_tool_handler(
    func: Optional[Callable] = None,
    *,
    error_types: Tuple[Type[Exception], ...] = (Exception,),
    include_traceback: bool = False,
    log_duration: bool = False,
    rate_limit_bucket: Optional[str] = "tool_calls",
):
    """Decorator that wraps MCP tool functions with standardized error handling.

    The decorated function should return a dict (not JSON string).
    The decorator handles:

    1. Wrapping return dict with ``{"success": True, ...}``
    2. Catching exceptions and returning ``{"success": False, "error": ..., "error_type": ...}``
    3. JSON serialization of the final response via shared ``_serialize()``
    4. Optional execution timing via ``log_duration``

    Args:
        func: The tool function to decorate (auto-detected when used without parens).
        error_types: Tuple of exception types to catch (default: all ``Exception``).
            Only ``Exception`` subclasses are accepted; ``BaseException`` subclasses
            like ``KeyboardInterrupt`` and ``SystemExit`` are never caught.
        include_traceback: If True, include last 500 chars of traceback in error response.
        log_duration: If True, add ``duration_ms`` field to response.
        rate_limit_bucket: Token bucket this tool draws from before running,
            or None to exempt the tool entirely. Defaults to ``"tool_calls"``.
            Enforcement is opt-in at runtime via ENABLE_RATE_LIMITING=1, so
            the default changes nothing until an operator switches it on.

            Pass ``"llm_calls"`` for tools that bill per invocation, and None
            for pure local computation. Exempting pure computation is not
            cosmetic: a shared bucket drained by cheap in-process helpers
            leaves no budget for the calls that actually reach a quota.

            The bucket is per server process, which is the unit that matters
            for protecting an upstream quota -- one server must not be able to
            exhaust an API allowance on its own.

    Returns:
        Decorated function that returns a JSON string.

    Usage::

        # Simple (no args):
        @mcp.tool()
        @mcp_tool_handler
        def my_tool(x: str) -> dict:
            return {"result": x.upper()}

        # With options:
        @mcp.tool()
        @mcp_tool_handler(include_traceback=True, log_duration=True)
        def my_tool(x: str) -> dict:
            return {"result": x.upper()}
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> str:
            """Wrapper that handles serialization and error catching.

            Returns:
                JSON string with ``success`` field and tool results or error details.
            """
            if rate_limit_bucket is not None:
                verdict = _rate_limit_verdict(rate_limit_bucket)
                if not verdict.get("allowed", True):
                    return _serialize({
                        "success": False,
                        "error": "Rate limit exceeded for bucket '{}'. "
                                 "Retry in {} seconds.".format(
                                     rate_limit_bucket,
                                     verdict.get("retry_after"),
                                 ),
                        "error_type": "RateLimitExceeded",
                        "bucket": rate_limit_bucket,
                        "retry_after": verdict.get("retry_after"),
                    })

            # Monotonic, not wall-clock: an NTP correction mid-call would
            # otherwise produce a negative or wildly inflated duration_ms.
            start = time.monotonic() if log_duration else 0.0

            try:
                result = fn(*args, **kwargs)

                # If function already returns a string, pass through
                # (backward compat for gradual migration)
                if isinstance(result, str):
                    return result

                # If function returns a dict, wrap with success.
                # Copied rather than mutated in place: the tool may have
                # returned a cached or module-level dict, and stamping
                # success/duration_ms onto it would corrupt later reads.
                if isinstance(result, dict):
                    payload = dict(result)
                    if "success" not in payload:
                        payload["success"] = True

                    if log_duration:
                        payload["duration_ms"] = round(
                            (time.monotonic() - start) * 1000
                        )

                    return _serialize(payload)

                # If function returns None, treat as success with no data
                if result is None:
                    payload = {"success": True}
                    if log_duration:
                        payload["duration_ms"] = round(
                            (time.monotonic() - start) * 1000
                        )
                    return _serialize(payload)

                # Anything else, wrap it
                return _serialize({"success": True, "data": result})

            except error_types as e:
                err_payload = {
                    "success": False,
                    # str(e) is empty for exceptions raised without a message,
                    # which would otherwise emit "error": "" and tell the model
                    # nothing at all about the failure.
                    "error": str(e) or type(e).__name__,
                    "error_type": type(e).__name__,
                }
                if include_traceback:
                    err_payload["traceback"] = traceback.format_exc()[-500:]
                if log_duration:
                    err_payload["duration_ms"] = round(
                        (time.monotonic() - start) * 1000
                    )
                return _serialize(err_payload)

        return wrapper

    # Support both @mcp_tool_handler and @mcp_tool_handler(...)
    if func is not None:
        return decorator(func)
    return decorator


def validate_params(*required_params: str):
    """Decorator that validates required parameters are not None before execution.

    Only checks that required parameters are present and not ``None``.
    Does NOT reject falsy values like ``0``, ``False``, or ``""`` --
    those are valid parameter values.

    Args:
        *required_params: Names of parameters that must not be ``None``.

    Returns:
        Decorator function.

    Raises:
        ValueError: If any required parameter is missing or ``None``.

    Usage::

        @mcp.tool()
        @validate_params("session_id", "branch")
        @mcp_tool_handler
        def my_tool(session_id: str, branch: str) -> dict:
            ...

    Note:
        This decorator only checks keyword arguments. Ensure the framework
        passes parameters as kwargs (FastMCP does this by default).
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Union[str, dict]:
            """Validates required params are not None before calling the tool."""
            missing = [
                p for p in required_params
                if p not in kwargs or kwargs[p] is None
            ]
            if missing:
                raise ValueError(
                    f"Missing required parameters: {', '.join(missing)}"
                )
            return fn(*args, **kwargs)
        return wrapper
    return decorator
