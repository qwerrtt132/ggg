"""A lock on the data directory, so only one server at a time can use it.

The lock is an OS file lock, so it is released when the process exits, even after a crash. Windows has no
fcntl; there the lock is not enforced, and the README says so.
"""

from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None


def acquire_data_lock(data_dir):
    """Returns an open file that holds the lock. Keep it open for as long as the server runs."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(data_dir / "agent_team.lock", "a+")
    if fcntl is None:
        return handle
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(f"the data directory {data_dir} is already in use by another agent-team server") from None
    return handle
