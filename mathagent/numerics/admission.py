# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ENPC / SquareHarness

"""Optional process-wide numerical capacity shared by separate SH accounts.

The administrator creates the directory and lock files; clients only lock them.
Unset configuration preserves the ordinary single-user execution path.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import time

WAIT_SECONDS = 45


class AdmissionUnavailable(RuntimeError):
    pass


class AdmissionCancelled(Exception):
    pass


def _open_slots(directory, count):
    files, folder = [], None
    try:
        if (not directory or not Path(directory).is_absolute() or '..' in Path(directory).parts
                or not 1 <= count <= 8):
            raise ValueError('Invalid numerical admission settings')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        folder = os.open('/', flags)
        for part in Path(directory).parts[1:]:
            child = os.open(part, flags, dir_fd=folder)
            os.close(folder)
            folder = child
        info = os.fstat(folder)
        if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
            raise ValueError('Untrusted numerical lock directory')
        for index in range(count):
            descriptor = os.open(f'slot-{index}.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
            files.append(descriptor)
            entry = os.fstat(descriptor)
            if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
                    or entry.st_uid not in (0, os.getuid()) or entry.st_gid != info.st_gid
                    or stat.S_IMODE(entry.st_mode) not in (0o600, 0o660)):
                raise ValueError('Untrusted numerical lock file')
        return files
    except (OSError, ValueError):
        for descriptor in files:
            os.close(descriptor)
        raise AdmissionUnavailable('Server numerical admission is not configured safely; ask the administrator.') from None
    finally:
        if folder is not None:
            os.close(folder)


@contextmanager
def slot(cancel=None):
    directory, configured_count = os.environ.get('SH_NUMERICS_SLOTS_DIR'), os.environ.get('SH_NUMERICS_SLOTS')
    if directory is None and configured_count is None:
        yield
        return
    try:
        import fcntl
    except ImportError:
        raise AdmissionUnavailable('Shared numerical admission requires POSIX file locking.') from None
    try:
        count = int('2' if configured_count is None else configured_count)
    except ValueError:
        raise AdmissionUnavailable('Server numerical admission has an invalid worker count.') from None
    files = _open_slots(directory, count)
    deadline = time.monotonic() + WAIT_SECONDS
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise AdmissionCancelled
            if time.monotonic() >= deadline:
                raise AdmissionUnavailable('Server numerical workers are busy; try again shortly.')
            for descriptor in files:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                except OSError:
                    raise AdmissionUnavailable('Server numerical workers could not be locked safely.') from None
                break
            else:
                cancel.wait(0.05) if cancel is not None else time.sleep(0.05)
                continue
            if cancel is not None and cancel.is_set():
                raise AdmissionCancelled
            yield
            return
    finally:
        for descriptor in files:
            os.close(descriptor)
