"""Low-level file helpers: per-file locking and atomic replacement."""
import contextlib
import os
import tempfile
import threading

try:  # POSIX (Linux / macOS) – used in production
    import fcntl
except ImportError:  # pragma: no cover - Windows dev machines
    fcntl = None

_thread_locks = {}
_thread_locks_guard = threading.Lock()


def _thread_lock(path):
    with _thread_locks_guard:
        lock = _thread_locks.get(path)
        if lock is None:
            lock = _thread_locks[path] = threading.Lock()
        return lock


@contextlib.contextmanager
def file_lock(target_path):
    """Exclusive lock for `target_path`, safe across threads *and* processes.

    A sidecar ``<file>.lock`` is used so the workbook itself can be replaced
    atomically while the lock is held.
    """
    lock_path = target_path + ".lock"
    tlock = _thread_lock(lock_path)
    with tlock:
        if fcntl is None:
            yield
            return
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def backup_path(path):
    """``annotator_x.xlsx`` -> ``annotator_x.bak.xlsx`` (openpyxl needs the .xlsx suffix)."""
    root, ext = os.path.splitext(path)
    return root + ".bak" + ext


def atomic_write(path, write_fn, keep_backup=True):
    """Write a file atomically.

    ``write_fn(tmp_path)`` writes the new content to a temporary file in the
    same directory; it is fsynced and then moved over ``path`` with
    ``os.replace``. A crash at any moment leaves either the old or the new
    file in place, never a half-written one. The previous version is kept as
    ``<name>.bak.xlsx`` for recovery.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".xlsx", dir=directory)
    os.close(fd)
    try:
        write_fn(tmp)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        if keep_backup and os.path.exists(path):
            bak_fd, bak_tmp = tempfile.mkstemp(prefix=".bak-", dir=directory)
            os.close(bak_fd)
            try:
                with open(path, "rb") as src, open(bak_tmp, "wb") as dst:
                    dst.write(src.read())
                os.replace(bak_tmp, backup_path(path))
            except OSError:
                with contextlib.suppress(OSError):
                    os.remove(bak_tmp)
        os.replace(tmp, path)
        _fsync_dir(directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _fsync_dir(directory):
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
