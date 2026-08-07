"""
Structured MCP error responses for consistent error handling across all MCP servers.

NOTE: For new code, prefer using base.response (MCPResponse, success, error, to_json)
and base.decorators (mcp_tool_handler). This module is kept for backward compatibility
with existing callers outside the MCP server layer.
"""

import json
import os
import traceback
from datetime import datetime

# Re-export from the shared response module for convenience. The package is
# named "mcp_base" in this repository and "base" in every vendored copy, so
# both spellings are attempted; when neither resolves the standalone helpers
# below are still fully functional.
try:
    from base.response import to_json, success as mcp_success, error as mcp_error  # noqa: F401
except ImportError:
    try:
        from mcp_base.response import to_json, success as mcp_success, error as mcp_error  # noqa: F401
    except ImportError:
        pass  # Standalone mode: the helpers defined below are self-contained


def mcp_error_response(error_type, message, details=None, suggestion=None):
    """Build a structured error response dict for MCP tool returns.

    Args:
        error_type: Short error category (e.g. "NOT_FOUND", "VALIDATION_ERROR", "IO_ERROR")
        message: Human-readable error description
        details: Optional dict with extra context
        suggestion: Optional fix suggestion string

    Returns:
        JSON string of the error response
    """
    response = {
        "status": "ERROR",
        "error_type": error_type,
        "message": message,
        "timestamp": datetime.now().isoformat(),
    }
    if details:
        response["details"] = details
    if suggestion:
        response["suggestion"] = suggestion
    return json.dumps(response, indent=2, default=str)


def mcp_success_response(data, message=None):
    """Build a structured success response.

    Args:
        data: The response payload dict
        message: Optional human-readable status message

    Returns:
        JSON string of the success response
    """
    response = {
        "status": "OK",
        "timestamp": datetime.now().isoformat(),
    }
    if message:
        response["message"] = message
    response["data"] = data
    return json.dumps(response, indent=2, default=str)


def mcp_safe_execute(func, error_type="INTERNAL_ERROR", fallback=None):
    """Execute a function with automatic error wrapping.

    The traceback is attached only when ENABLE_DEBUG_TRACEBACKS is set to "1"
    or "true". A traceback names internal modules, file paths and frame locals,
    and this response is handed straight back to the model and the user, so it
    is opt-in rather than the default.

    Args:
        func: Callable to execute
        error_type: Error type string for failures
        fallback: Optional fallback return value

    Returns:
        Function result on success, or error response string on failure
    """
    try:
        return func()
    except Exception as e:
        details = None
        if os.environ.get("ENABLE_DEBUG_TRACEBACKS", "").lower() in ("1", "true"):
            details = {"traceback": traceback.format_exc()[-500:]}
        return mcp_error_response(
            error_type=error_type,
            message=str(e) or type(e).__name__,
            details=details,
        )
