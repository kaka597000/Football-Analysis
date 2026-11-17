"""Cross-platform advisory locks for append-only publication stores."""
from contextlib import contextmanager
import os

@contextmanager
def exclusive_lock(path):
    with open(path, 'a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            # Windows permits byte-range locks past EOF. Writing a seed byte
            # before acquiring the lock races with another process's lock.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
