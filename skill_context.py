# skill_context.py
# Skill-aware context loader for Domain 46 UML & Diagram Engineering knowledge.
# Compatible: Python 3.11+, stdlib only, ASCII encoding safe (cp1252 compliant).
# Place at: mcp-uml-diagram/skill_context.py  OR  mcp-drawio-diagram/skill_context.py
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

GLOBAL_LIBRARY_PATH = os.environ.get(
    "GLOBAL_LIBRARY_PATH",
    r"C:\Users\techd\Documents\workspace-spring-tool-suite-4-4.27.0-new\claude-global-library",
)

_CACHE_TTL_SECONDS = 300.0
_CACHE = {}  # type: Dict[str, Tuple[Any, float]]
_SKILL_MAX_BYTES = int(os.environ.get("SKILL_MAX_BYTES", "524288"))  # 512 KB default

_SKILL_PATH_TEMPLATE = os.path.join("skills", "{name}", "SKILL.md")
_KG_PATH_TEMPLATE = os.path.join("knowledge-graph", "{domain}", "skills.json")

_VALID_SLUG_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _safe_join(base_dir, *parts):
    # type: (str, *str) -> Optional[str]
    """Return resolved path only if it stays within base_dir. Return None on traversal attempt.

    Uses os.path.abspath to resolve symbolic links and normalize separators on
    both Windows and POSIX before comparing prefix. The comparison uses
    base + os.sep so that a base of '/foo' does not incorrectly pass '/foobar'.

    Args:
        base_dir: Absolute base directory that the result must remain within.
        *parts: Path components to join under base_dir.

    Returns:
        Absolute path string if the resolved candidate is within base_dir,
        or None if a path traversal attempt is detected.
    """
    base = os.path.abspath(base_dir)
    candidate = os.path.abspath(os.path.join(base_dir, *parts))
    if not candidate.startswith(base + os.sep) and candidate != base:
        return None
    return candidate


def _is_valid_slug(value):
    # type: (str) -> bool
    """Return True if value contains only alphanumeric characters, hyphens, and underscores.

    This secondary guard prevents slugs with path separators, dots, or special
    characters from even reaching _safe_join. Applied to both skill_name and
    domain parameters.

    Args:
        value: The slug string to validate.

    Returns:
        True if all characters are in the permitted set, False otherwise.
    """
    if not value:
        return False
    return all(ch in _VALID_SLUG_CHARS for ch in value)


def _get_cached(key):
    # type: (str) -> Optional[Any]
    """Return cached value if present and not expired, otherwise None.

    Compares current time against the stored timestamp using _CACHE_TTL_SECONDS.
    Returns None for both cache miss and expired entries.

    Args:
        key: Unique cache key string.

    Returns:
        Cached value if valid, None on miss or expiry.
    """
    entry = _CACHE.get(key)
    if entry is None:
        return None
    value, stored_at = entry
    if (time.time() - stored_at) >= _CACHE_TTL_SECONDS:
        return None
    return value


def _set_cached(key, value):
    # type: (str, Any) -> None
    """Store value in the module-level cache with the current timestamp.

    Args:
        key: Unique cache key string.
        value: Any serialisable value to store alongside the timestamp.
    """
    _CACHE[key] = (value, time.time())


def _read_cached(cache_key, reader_fn):
    # type: (str, Callable[[], Any]) -> Any
    """Return cached value or invoke reader_fn, cache the result, and return it.

    On any exception from reader_fn the exception is suppressed and None is
    returned. This keeps callers' graceful-degradation guarantee intact.

    Args:
        cache_key: Unique string key for this cached item.
        reader_fn: Zero-argument callable that returns the value to cache.

    Returns:
        Cached or freshly-read value from reader_fn, or None on any error.
    """
    hit = _get_cached(cache_key)
    if hit is not None:
        return hit
    try:
        result = reader_fn()
        _set_cached(cache_key, result)
        return result
    except Exception:
        return None


def get_skill_context(skill_name):
    # type: (str) -> str
    """Read M1-M6 sections and Response Rules from a Domain 46 skill SKILL.md file.

    Applies two-layer security: (1) _is_valid_slug() rejects skill names containing
    path separators or dots; (2) _safe_join() rejects any path that escapes
    GLOBAL_LIBRARY_PATH after abspath normalization. Returns empty string on
    ANY error including missing file, path traversal, or read failure. Results
    are cached for _CACHE_TTL_SECONDS (300 s) at module level.

    Skill files are expected at:
        {GLOBAL_LIBRARY_PATH}/skills/{skill_name}/SKILL.md

    Args:
        skill_name: Exact skill directory name (e.g., "uml-class-diagram-core").
                    Must contain only alphanumeric characters, hyphens, and underscores.
                    Must not contain path separators, dots, or '..' components.

    Returns:
        Full text content of SKILL.md as a UTF-8 string on success,
        or "" on any error (invalid slug, path traversal, file not found,
        permission denied, decode error).
    """
    if not _is_valid_slug(skill_name):
        return ""

    cache_key = "skill:" + skill_name
    hit = _get_cached(cache_key)
    if hit is not None:
        return hit

    safe_path = _safe_join(GLOBAL_LIBRARY_PATH, "skills", skill_name, "SKILL.md")
    if safe_path is None:
        return ""

    def _reader():
        # type: () -> str
        p = Path(safe_path)
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(_SKILL_MAX_BYTES)

    result = _read_cached(cache_key, _reader)
    if result is None:
        return ""
    return result


def get_domain_context(domain="uml-diagram-engineering"):
    # type: (str) -> Dict[str, Any]
    """Read Domain KG metadata from knowledge-graph/{domain}/skills.json.

    Applies two-layer security: (1) _is_valid_slug() rejects domain slugs
    containing path separators or dots; (2) _safe_join() rejects any path
    that escapes GLOBAL_LIBRARY_PATH. Returns empty dict on ANY error.
    Results are cached for _CACHE_TTL_SECONDS (300 s) at module level.

    The file is read from:
        {GLOBAL_LIBRARY_PATH}/knowledge-graph/{domain}/skills.json

    Args:
        domain: Domain slug (e.g., "uml-diagram-engineering").
                Must contain only alphanumeric characters, hyphens, and underscores.
                Must not contain path separators or '..' components.

    Returns:
        Parsed dict from skills.json on success (structure: list wrapped in dict
        under key "skills" for uniform return type), or {} on any error.
    """
    if not _is_valid_slug(domain):
        return {}

    cache_key = "domain:" + domain
    hit = _get_cached(cache_key)
    if hit is not None:
        return hit

    safe_path = _safe_join(
        GLOBAL_LIBRARY_PATH, "knowledge-graph", domain, "skills.json"
    )
    if safe_path is None:
        return {}

    def _reader():
        # type: () -> Dict[str, Any]
        p = Path(safe_path)
        raw = p.read_text(encoding="utf-8", errors="replace")
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return {"skills": parsed, "domain": domain, "count": len(parsed)}
        if isinstance(parsed, dict):
            return parsed
        return {}

    result = _read_cached(cache_key, _reader)
    if result is None:
        return {}
    return result
