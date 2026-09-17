import os
import time
import json
import uuid
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

_local = threading.local()

def safe_unlink(path: Path | str, max_attempts: int = 5) -> None:
    path = Path(path)
    for attempt in range(max_attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == max_attempts - 1:
                raise
            time.sleep(0.05)
        except OSError:
            pass

def safe_replace(src: Path | str, dst: Path | str, max_attempts: int = 10) -> None:
    src, dst = Path(src), Path(dst)
    for attempt in range(max_attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == max_attempts - 1:
                raise
            time.sleep(0.05)

@contextmanager
def simple_file_lock(filepath: Path | str, timeout: float = 30.0):
    filepath = Path(filepath)
    lock_path = filepath.with_suffix(filepath.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if not hasattr(_local, "held_locks"):
        _local.held_locks = {}

    canon_key = str(lock_path.resolve()).lower()

    if _local.held_locks.get(canon_key, 0) > 0:
        _local.held_locks[canon_key] += 1
        try:
            yield
        finally:
            _local.held_locks[canon_key] -= 1
        return

    start = time.monotonic()
    backoff = 0.005
    max_backoff = 0.05
    acquired = False

    while time.monotonic() - start < timeout:
        try:
            # Check for stale lock (Gap 5: Crash recovery)
            if lock_path.exists():
                try:
                    if time.time() - lock_path.stat().st_mtime > 10.0:
                        try:
                            safe_unlink(lock_path)
                        except OSError:
                            pass
                except OSError:
                    pass

            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            acquired = True
            break
        except (FileExistsError, PermissionError):
            time.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)

    if not acquired:
        raise TimeoutError(f"Timed out after {timeout:.1f}s waiting for lock: {lock_path}")

    _local.held_locks[canon_key] = 1
    try:
        yield
    finally:
        _local.held_locks[canon_key] = 0
        safe_unlink(lock_path)

@contextmanager
def locked_read(path: Path | str, default: Any = None, parse_json: Optional[bool] = None, timeout: float = 30.0):
    path = Path(path)
    with simple_file_lock(path, timeout=timeout):
        if not path.exists():
            yield default
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()

        should_parse = parse_json if parse_json is not None else (path.suffix.lower() == ".json")
        if should_parse:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                data = default if default is not None else raw
        else:
            data = raw

        yield data

@contextmanager
def locked_write(path: Path | str, content: Any, timeout: float = 30.0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with simple_file_lock(path, timeout=timeout):
        uid = uuid.uuid4().hex
        tmp_path = path.with_suffix(f".{uid}.tmp")

        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            if isinstance(content, (dict, list)):
                json.dump(content, f, indent=2, sort_keys=True, ensure_ascii=True)
            elif isinstance(content, str):
                f.write(content)
            else:
                try:
                    json.dump(content, f, indent=2, sort_keys=True, ensure_ascii=True)
                except (TypeError, ValueError):
                    f.write(str(content))
            f.flush()
            os.fsync(f.fileno())

        safe_replace(tmp_path, path)
        yield

def locked_append(path: Path | str, text: str, timeout: float = 30.0) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with simple_file_lock(path, timeout=timeout):
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)

def read_locked(path: Path | str, default: Any = None, parse_json: Optional[bool] = None, timeout: float = 30.0) -> Any:
    with locked_read(path, default=default, parse_json=parse_json, timeout=timeout) as data:
        return data

def write_locked(path: Path | str, content: Any, timeout: float = 30.0) -> None:
    with locked_write(path, content, timeout=timeout):
        pass


