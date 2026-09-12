import os
import time
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def simple_file_lock(filepath: Path, timeout: int = 10):
    lock_path = filepath.with_suffix(filepath.suffix + ".lock")
    start = time.time()
    while True:
        try:
            # Atomic operation on Windows and Unix
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - start > timeout:
                # Break the stale lock (Crash recovery - Gap 5)
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
            time.time()
            time.sleep(0.1)
    
    try:
        yield
    finally:
        try:
            os.remove(lock_path)
        except OSError:
            pass

