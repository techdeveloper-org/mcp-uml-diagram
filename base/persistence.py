"""
MCP Persistence Layer - Repository Pattern for file-based data storage.

Design Patterns:
  - Repository Pattern: ``AtomicJsonStore`` encapsulates JSON file CRUD
  - Append-Only Log:    ``JsonlAppender`` for structured event logging
  - Singleton:          ``SessionIdResolver`` caches current session ID

Replaces duplicated file I/O patterns across 6+ MCP servers:
  - Atomic write (write .tmp -> rename): 4 servers
  - JSONL append: 3 servers
  - Session ID resolution: 3 servers
  - State load/save: 4 servers

Windows-Safe: ASCII only (cp1252 compatible)
"""

import copy
import hashlib
import json
import os
import random
import shutil
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Tuple


# Bounded retry for the publish step of an atomic save. On Windows, os.replace
# raises PermissionError (WinError 5) when any other process or thread holds an
# open handle to the destination -- which happens routinely when two writers
# publish the same file at once, or when a virus scanner or editor has it open.
# POSIX rename has no such failure mode, so on Linux/macOS this loop runs once.
_REPLACE_MAX_ATTEMPTS = 5
_REPLACE_BACKOFF_SECONDS = 0.02

# Compare-and-swap settings for ``AtomicJsonStore.modify``.
#
# The compare and the publish must happen together or the compare proves
# nothing: two writers can each read the same version, each conclude the file is
# unchanged, and each replace it, which is the very lost update the version
# check exists to prevent. An O_EXCL claim file makes that pair indivisible.
# It is held only across compare-and-publish, never across the caller's mutator,
# so a slow or failing mutator cannot block another process.
#
# The claim is reclaimed once it is older than the stale threshold, so a process
# killed mid-publish cannot strand the file permanently. The threshold is far
# larger than a publish (a hash comparison plus a rename) to make reclaiming a
# live claim implausible.
# Retry backoff is fully jittered: each waiter sleeps a random duration in
# [0, window) rather than the window itself. Losers of a race are released at
# the same instant and would otherwise back off in lock-step and collide again
# on every subsequent attempt, which is how a transient collision turns into
# exhausted retries. Measured on 8 processes x 25 increments: unjittered
# backoff at 8 attempts failed 43 of 200 updates; jittered at the settings
# below completes all 200.
_CAS_MAX_ATTEMPTS = 25
_CAS_BACKOFF_SECONDS = 0.005
_CAS_BACKOFF_CAP_SECONDS = 0.25
_CAS_CLAIM_TIMEOUT_SECONDS = 10.0
_CAS_CLAIM_STALE_SECONDS = 30.0


def _cas_backoff(attempt: int) -> float:
    """Return a fully jittered backoff delay for a lost compare-and-swap race.

    The exponential window is capped so a long retry chain cannot grow the
    delay without bound, and the actual sleep is drawn uniformly from that
    window so concurrent losers desynchronize instead of retrying together.

    Args:
        attempt: Zero-based attempt index that just failed.

    Returns:
        Seconds to sleep before the next attempt.
    """
    window = min(_CAS_BACKOFF_CAP_SECONDS, _CAS_BACKOFF_SECONDS * (2 ** attempt))
    return random.uniform(0.0, window)


class ConcurrentModificationError(RuntimeError):
    """Raised when a read-modify-write cycle loses too many races in a row.

    Signals sustained contention on one file rather than a transient
    collision. Callers should surface it rather than retry blindly: the
    modify loop has already retried.
    """


def _log_warning(event: str, **fields: Any) -> None:
    """Emit a single-line structured warning to stderr.

    Defined locally rather than imported so this module stays self-contained
    when it is vendored into a server repo as ``base/persistence.py``.
    Output is forced to ASCII because stderr on Windows is cp1252 and an
    exception message containing a non-Latin-1 character would otherwise raise
    ``UnicodeEncodeError`` from inside the error path itself.

    Args:
        event: Short machine-readable event name.
        **fields: Additional key=value context to include.
    """
    parts = ["level=WARNING", "component=mcp_base.persistence", "event=" + event]
    parts.extend("{}={}".format(key, value) for key, value in fields.items())
    line = " ".join(parts)
    try:
        sys.stderr.write(line.encode("ascii", "backslashreplace").decode("ascii") + "\n")
    except (OSError, ValueError):
        pass  # stderr is closed or detached; the caller's operation must still proceed


class AtomicJsonStore:
    """Thread-safe, atomic JSON file persistence with backup support.

    Repository pattern -- encapsulates all read/write/backup logic
    for a single JSON file. Uses write-to-temp-then-rename for
    crash safety (no partial writes on interruption).

    The ``_dir_created`` flag avoids redundant ``mkdir`` syscalls on
    the hot write path after the first successful save.

    Args:
        path: Path to the JSON file to manage.
        default_factory: Callable returning default dict when file is missing.
            Defaults to ``dict`` (returns empty dict).

    Example::

        store = AtomicJsonStore(Path("~/.claude/memory/state.json"))

        # Read-modify-write. Use this whenever the new value depends on the
        # old one: it will not overwrite a concurrent writer's update.
        store.modify(lambda d: d.update(count=d.get("count", 0) + 1),
                     default={"count": 0})

        # Blind overwrite. Correct only when the new content does not depend
        # on what is already there; otherwise a concurrent update is lost.
        store.save({"count": 0})
    """

    __slots__ = ("_path", "_default_factory", "_dir_created")

    def __init__(self, path: Path, default_factory: Optional[Callable] = None):
        self._path = Path(path)
        self._default_factory = default_factory or dict
        self._dir_created = False

    @property
    def path(self) -> Path:
        """The filesystem path of the backing JSON file."""
        return self._path

    @property
    def exists(self) -> bool:
        """Whether the backing file currently exists on disk."""
        return self._path.exists()

    def load(self, default: Optional[dict] = None) -> dict:
        """Load JSON data from file with automatic backup fallback.

        Attempts to read the primary file first. If it is missing or
        corrupted (invalid JSON), falls back to the ``.bak`` backup.
        If both fail, returns the provided default or calls the
        ``default_factory``.

        Uses try/except instead of existence checks to avoid TOCTOU races.

        The default is deep-copied, not shallow-copied. A shallow copy shares
        every nested list and dict with the caller's original, so mutating the
        loaded data would write straight through into the default object. Two
        consequences, both observed: a module-level default accumulates state
        for the life of the process, and a ``modify`` attempt that loses its
        race leaks its mutations into the retry that follows it.

        Args:
            default: Explicit default dict to return if file is missing.
                Takes precedence over ``default_factory`` when provided.
                Safe to pass a shared constant; it is never mutated.

        Returns:
            Parsed dict from file, backup, or a deep copy of the default.
        """
        # Try primary file
        data = self._try_read(self._path)
        if data is not None:
            return data

        # Try .bak backup
        bak = self._backup_path()
        data = self._try_read(bak)
        if data is not None:
            _log_warning(
                "primary_json_unreadable_using_backup",
                path=self._path.name,
                backup=bak.name,
            )
            return data

        # Return default
        if default is not None:
            return copy.deepcopy(default)
        return self._default_factory()

    def _backup_path(self) -> Path:
        """Path of the ``.bak`` sibling for this store."""
        return self._path.with_name(self._path.name + ".bak")

    def _temp_path(self) -> Path:
        """Path of a temporary file unique to this write attempt.

        The temp name carries the full target filename plus the process id and
        a random token. A fixed name such as ``state.tmp`` is shared by every
        writer of the same file, so two concurrent saves interleave their bytes
        into one temp file and whichever rename lands second publishes a
        corrupted mixture. It also collides across stores whose paths differ
        only by suffix, and on Windows ``os.replace`` raises ``PermissionError``
        when another process still holds the temp file open.

        Returns:
            A path in the same directory as the target, so the final replace
            stays within one filesystem and therefore stays atomic.
        """
        token = "{}.{}".format(os.getpid(), uuid.uuid4().hex[:8])
        return self._path.with_name("{}.{}.tmp".format(self._path.name, token))

    def _publish(self, temp: Path) -> None:
        """Move a fully-written temp file over the target, retrying on Windows.

        Retrying is safe because the temp file is complete before the first
        attempt: every attempt publishes the identical bytes, so the operation
        is idempotent and a retry can neither duplicate nor partially apply it.

        Args:
            temp: Path of the completed temp file to publish.

        Raises:
            PermissionError: If every attempt was blocked by an open handle.
            OSError: For any other failure to move the file into place.
        """
        for attempt in range(_REPLACE_MAX_ATTEMPTS):
            try:
                temp.replace(self._path)
                return
            except PermissionError:
                if attempt == _REPLACE_MAX_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_SECONDS * (2 ** attempt))

    def save(self, data: dict, backup: bool = False) -> None:
        """Save data atomically via write-to-temp-then-rename.

        Creates parent directories on first call, then caches the
        result to skip ``mkdir`` on subsequent saves (hot-path optimization).

        This is an unconditional overwrite. The temp file is unique per write
        attempt, so concurrent writers cannot corrupt each other's staging
        file, and a reader never sees a partial write -- but the last writer
        still wins whole, discarding any update made since this caller read.

        Use ``save`` only when the new content does not depend on the current
        content. When it does -- incrementing a counter, appending to a list,
        setting a field alongside others -- use ``modify``, which detects a
        concurrent write and retries instead of discarding it.

        Args:
            data: Dictionary to serialize and persist.
            backup: If True, copy current file to ``.bak`` before overwriting.
                Backup failures are reported to stderr but do not prevent the save.
        """
        if not self._dir_created:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._dir_created = True

        if backup and self._path.exists():
            bak = self._backup_path()
            try:
                shutil.copy2(str(self._path), str(bak))
            except OSError as exc:
                _log_warning(
                    "backup_copy_failed",
                    path=self._path.name,
                    error=type(exc).__name__,
                    detail=str(exc),
                )

        temp = self._temp_path()
        try:
            temp.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
            self._publish(temp)
        except BaseException:
            try:
                temp.unlink()
            except OSError:
                _log_warning("temp_file_cleanup_failed", temp=temp.name)
            raise

    def _read_raw(self) -> Optional[bytes]:
        """Read the primary file's bytes without parsing them.

        Returns:
            The raw bytes, or None if the file is absent or unreadable.
        """
        try:
            return self._path.read_bytes()
        except (OSError, ValueError):
            return None

    @staticmethod
    def _version_of(raw: Optional[bytes]) -> Optional[str]:
        """Derive a version token from a file's raw bytes.

        Content hashing is used rather than mtime because mtime resolution is
        coarse enough on Windows and on some filesystems that two writes within
        the same tick are indistinguishable, which would let a lost update pass
        the version check. Hashing the bytes has no such blind spot.

        Bytes are hashed whether or not they parse as JSON, so a corrupt
        primary file still yields a stable token and a cycle that read through
        to the backup can still detect the primary changing underneath it.

        Args:
            raw: File bytes, or None when the file is absent.

        Returns:
            A hex digest, or None to represent "file absent".
        """
        if raw is None:
            return None
        return hashlib.sha256(raw).hexdigest()

    def load_versioned(self, default: Optional[dict] = None) -> Tuple[dict, Optional[str]]:
        """Load data together with a version token for compare-and-swap.

        Args:
            default: Explicit default dict to return if the file is missing.

        Returns:
            A ``(data, version)`` pair. Pass ``version`` unchanged to
            ``save_if_unchanged`` to publish only if nobody else has written
            since. A version of None means the file was absent at read time.
        """
        raw = self._read_raw()
        version = self._version_of(raw)
        if raw is not None:
            try:
                return json.loads(raw.decode("utf-8")), version
            except (ValueError, UnicodeDecodeError):
                pass
        return self.load(default=default), version

    def _claim_path(self) -> Path:
        """Path of the compare-and-publish claim file for this store."""
        return self._path.with_name(self._path.name + ".caslock")

    @contextmanager
    def _publish_claim(self) -> Iterator[None]:
        """Hold an exclusive claim across the compare-and-publish step.

        The claim is taken with ``O_CREAT | O_EXCL``, which is a genuine atomic
        insert-if-absent: exactly one caller can create the file, and the
        losers see FileExistsError rather than silently proceeding. A
        check-then-create would reintroduce the race this exists to close.

        Yields:
            None, with the claim held. It is always released on exit.

        Raises:
            TimeoutError: If the claim could not be taken within the timeout.
        """
        claim = self._claim_path()
        deadline = time.monotonic() + _CAS_CLAIM_TIMEOUT_SECONDS
        handle = None
        while handle is None:
            try:
                handle = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - claim.stat().st_mtime
                except OSError:
                    continue
                if age > _CAS_CLAIM_STALE_SECONDS:
                    _log_warning(
                        "cas_claim_stale_reclaimed",
                        path=self._path.name,
                        age_s=round(age, 1),
                    )
                    try:
                        claim.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "Timed out claiming {} after {}s".format(
                            claim.name, _CAS_CLAIM_TIMEOUT_SECONDS
                        )
                    )
                time.sleep(_CAS_BACKOFF_SECONDS)
            except OSError:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._dir_created = True
        try:
            yield
        finally:
            try:
                os.close(handle)
            except OSError:
                pass
            try:
                claim.unlink()
            except OSError:
                _log_warning("cas_claim_release_failed", path=claim.name)

    def save_if_unchanged(self, data: dict, expected_version: Optional[str],
                          backup: bool = False) -> bool:
        """Publish data only if the file still matches ``expected_version``.

        The serialized bytes are staged to a temp file before the claim is
        taken, so the exclusive section covers only the version comparison and
        the rename.

        Args:
            data: Dictionary to serialize and persist.
            expected_version: Token from ``load_versioned``. None means the
                caller expects the file to be absent.
            backup: If True, copy the current file to ``.bak`` before
                overwriting.

        Returns:
            True if the data was published, False if another writer had
            changed the file and nothing was written.
        """
        if not self._dir_created:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._dir_created = True

        temp = self._temp_path()
        try:
            temp.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
        except BaseException:
            try:
                temp.unlink()
            except OSError:
                _log_warning("temp_file_cleanup_failed", temp=temp.name)
            raise

        try:
            with self._publish_claim():
                if self._version_of(self._read_raw()) != expected_version:
                    return False
                if backup and self._path.exists():
                    bak = self._backup_path()
                    try:
                        shutil.copy2(str(self._path), str(bak))
                    except OSError as exc:
                        _log_warning(
                            "backup_copy_failed",
                            path=self._path.name,
                            error=type(exc).__name__,
                            detail=str(exc),
                        )
                self._publish(temp)
                return True
        except BaseException:
            raise
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    _log_warning("temp_file_cleanup_failed", temp=temp.name)

    def modify(self, fn: Callable[[dict], Any],
               default: Optional[dict] = None,
               backup: bool = False,
               max_attempts: int = _CAS_MAX_ATTEMPTS) -> dict:
        """Read-modify-write that will not silently lose a concurrent update.

        Loads the current data with a version token, applies ``fn``, and
        publishes only if the file is unchanged since the load. If another
        writer won the race, the whole cycle is retried against freshly loaded
        data rather than overwriting their work.

        ``fn`` therefore MUST be safe to run more than once, and must derive its
        result only from the dict it is given. A mutator that closes over state
        captured before the call, or that has side effects of its own, will
        behave incorrectly on retry. Mutate the passed dict and nothing else.

        ``fn`` runs outside the exclusive section, so a slow mutator delays only
        its own cycle and never blocks another process.

        Args:
            fn: Callback that receives the loaded dict and mutates it in place.
            default: Default dict if the file is missing.
            backup: If True, copy the current file to ``.bak`` before
                overwriting.
            max_attempts: How many times to retry after losing a race.

        Returns:
            The modified data dict that was successfully published.

        Raises:
            ConcurrentModificationError: If every attempt lost the race.
            TimeoutError: If the publish claim could not be taken.
        """
        for attempt in range(max_attempts):
            data, version = self.load_versioned(default=default)
            fn(data)
            if self.save_if_unchanged(data, version, backup=backup):
                return data
            time.sleep(_cas_backoff(attempt))

        _log_warning(
            "cas_modify_exhausted",
            path=self._path.name,
            attempts=max_attempts,
        )
        raise ConcurrentModificationError(
            "Lost {} consecutive races modifying {}".format(
                max_attempts, self._path.name
            )
        )

    def delete(self) -> bool:
        """Delete the backing file.

        Attempts the unlink directly rather than checking existence first: an
        ``exists()`` guard followed by ``unlink(missing_ok=True)`` reports True
        even when a concurrent deleter removed the file in between, which is
        the opposite of what the return value claims.

        Returns:
            True if this call deleted the file, False if it was already absent.
        """
        try:
            self._path.unlink()
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def _try_read(path: Path) -> Optional[dict]:
        """Attempt to read and parse a JSON file.

        Uses try/except (not existence check) to avoid TOCTOU races.

        Args:
            path: File path to attempt reading.

        Returns:
            Parsed dict on success, None on any failure (missing, corrupt, etc.).
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, FileNotFoundError, IOError,
                ValueError, OSError):
            pass
        return None


class JsonlAppender:
    """Append-only JSONL (JSON Lines) logger for structured event data.

    Each call to ``.append()`` writes one JSON object as a single line.
    Optimized for write-heavy, read-rarely patterns like tool tracking
    and optimization logging.

    The ``_dir_created`` flag avoids redundant ``mkdir`` on the hot append path.

    Args:
        path: Path to the JSONL file.

    Example::

        logger = JsonlAppender(Path("~/.claude/logs/tools.jsonl"))
        logger.append({"tool": "Read", "status": "success"})

        for entry in logger.read_all():
            print(entry["tool"])
    """

    __slots__ = ("_path", "_dir_created")

    def __init__(self, path: Path):
        self._path = Path(path)
        self._dir_created = False

    @property
    def path(self) -> Path:
        """The filesystem path of the JSONL file."""
        return self._path

    @property
    def exists(self) -> bool:
        """Whether the JSONL file currently exists on disk."""
        return self._path.exists()

    def append(self, entry: dict, auto_timestamp: bool = True) -> None:
        """Append a single JSON object as a line.

        Makes a shallow copy of the entry dict to avoid mutating the
        caller's data when adding the timestamp field.

        Args:
            entry: Dict to serialize as one JSON line.
            auto_timestamp: If True, add ``timestamp`` field if not already present.
        """
        if not self._dir_created:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._dir_created = True

        if auto_timestamp and "timestamp" not in entry:
            entry = {**entry, "timestamp": datetime.now().isoformat()}

        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _iter_entries(self):
        """Yield parsed entries one line at a time.

        Malformed lines and non-object lines are skipped rather than aborting
        the scan, so one corrupt append does not hide every later entry.

        Yields:
            Parsed dicts, in file order. Yields nothing if the file is missing.
        """
        try:
            handle = open(self._path, "r", encoding="utf-8")
        except FileNotFoundError:
            return
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict):
                    yield entry

    def read_all(self) -> List[dict]:
        """Read all entries by streaming line-by-line.

        Parsing is incremental, but the returned list still holds every entry
        in memory. Prefer ``read_filtered`` or ``count`` when only a subset or
        a total is needed from a large log.

        Returns:
            List of parsed dicts. Empty list if file is missing.
        """
        return list(self._iter_entries())

    def read_filtered(self, date: str = "", **filters: Any) -> List[dict]:
        """Read entries matching date and/or field filters.

        Filters while streaming, so only matching entries are retained in
        memory -- the previous implementation materialized the whole file
        first, which defeated the memory bound this method advertises.

        Args:
            date: ISO date prefix filter (e.g., ``"2026-03-17"``).
            **filters: Key-value pairs that entries must match exactly.

        Returns:
            List of matching entry dicts.
        """
        results = []
        for entry in self._iter_entries():
            if date:
                timestamp = entry.get("timestamp", "")
                if not isinstance(timestamp, str) or date not in timestamp:
                    continue
            if all(entry.get(k) == v for k, v in filters.items()):
                results.append(entry)
        return results

    def count(self) -> int:
        """Count total entries without loading all into memory.

        Returns:
            Number of non-empty lines in the file, or 0 if file is missing.
        """
        count = 0
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        count += 1
        except FileNotFoundError:
            pass
        return count


class SessionIdResolver:
    """Resolves current session ID from multiple file sources with caching.

    Singleton-style resolver that checks two file sources in order:

    1. ``.current-session.json`` (primary, key: ``current_session_id``)
    2. ``logs/session-progress.json`` (fallback, key: ``session_id``)

    Results are cached for 30 seconds to avoid repeated disk reads
    on the hot path (every tool call checks session ID).

    Args:
        config_dir: Root directory for session files.
            Only honored on first construction (singleton). Subsequent
            calls with a different path are ignored (the singleton is
            already initialized).

    Note:
        This is a singleton. Call ``SessionIdResolver.reset()`` to clear
        the instance (useful in tests).
    """

    _instance = None
    _instance_lock = threading.Lock()
    _CACHE_TTL = 30  # seconds

    def __new__(cls, config_dir: Optional[Path] = None):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self, config_dir: Optional[Path] = None):
        if self._initialized:
            return
        self._config_dir = config_dir or (Path.home() / ".claude" / "memory")
        self._cached_id = ""
        self._cache_time = 0.0
        self._initialized = True

    @property
    def current_session_file(self) -> Path:
        """Path to the primary session file (``.current-session.json``)."""
        return self._config_dir / ".current-session.json"

    @property
    def progress_file(self) -> Path:
        """Path to the fallback session file (``logs/session-progress.json``)."""
        return self._config_dir / "logs" / "session-progress.json"

    def get(self, force_refresh: bool = False) -> str:
        """Get current session ID with TTL-based caching.

        Returns the cached ID if within the 30-second TTL window.
        Otherwise re-resolves from disk.

        Args:
            force_refresh: If True, bypass cache and re-read from disk.

        Returns:
            Session ID string (e.g., ``"SESSION-20260317-143000-ABCD"``),
            or empty string if no valid session is found.
        """
        now = time.time()

        if not force_refresh and self._cached_id:
            if (now - self._cache_time) < self._CACHE_TTL:
                return self._cached_id

        sid = self._resolve()
        self._cached_id = sid
        self._cache_time = now
        return sid

    def invalidate(self) -> None:
        """Clear the cached session ID, forcing re-resolution on next ``get()``."""
        self._cached_id = ""
        self._cache_time = 0.0

    def _resolve(self) -> str:
        """Resolve session ID by checking file sources in priority order.

        Returns:
            Valid session ID or empty string.
        """
        sid = self._read_session_id(
            self.current_session_file, "current_session_id"
        )
        if sid:
            return sid

        sid = self._read_session_id(
            self.progress_file, "session_id"
        )
        if sid:
            return sid

        return ""

    @staticmethod
    def _read_session_id(path: Path, key: str) -> str:
        """Read a session ID from a JSON file by key.

        Uses try/except instead of existence check to avoid TOCTOU races.

        The parsed document and the value at ``key`` are both type-checked: a
        session file holding a JSON array, or a null/numeric session id, would
        otherwise raise ``AttributeError`` out of this resolver and crash the
        caller on what is meant to be a best-effort lookup.

        Args:
            path: JSON file to read.
            key: Dict key containing the session ID.

        Returns:
            Session ID if valid (starts with ``SESSION-``), empty string otherwise.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
            return ""

        if not isinstance(data, dict):
            return ""

        sid = data.get(key, "")
        if isinstance(sid, str) and sid.startswith("SESSION-"):
            return sid
        return ""

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (for testing).

        After calling this, the next ``SessionIdResolver()`` call
        creates a fresh instance with a new ``config_dir``.
        """
        cls._instance = None
