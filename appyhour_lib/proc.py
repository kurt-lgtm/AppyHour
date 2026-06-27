"""Parent-death watchdog for stdio child processes (the MCP servers).

Why this exists
===============
The AppyHour MCP servers run over FastMCP **stdio** — each is spawned as a CHILD
of the MCP client (Claude Desktop / Claude Code), one per client connection. A
stdio server is supposed to exit on stdin-EOF when the client disconnects. But
on Windows an *unclean* client shutdown — force-kill, closing the MSIX-packaged
app, a crash — frequently fails to deliver EOF to the child: it blocks forever
on a stdin read, gets reparented, and never exits. Each orphan keeps a live
``shipping.db`` connection open. On 2026-06-27 SIX such orphans had accumulated
across unclean session restarts (and they were part of the concurrent-writer
pileup behind that day's DB corruption).

Nothing bound the child's lifetime to the parent — no job object, no watchdog.
This module is that watchdog: a daemon thread polls whether the original parent
PID is still alive and HARD-exits the moment it isn't, releasing the DB handle.

Stdlib-only (os/threading/ctypes) so it stays inside appyhour_lib's pure-util
contract. Best-effort: it never raises into the host, and is disabled by setting
``APPYHOUR_NO_WATCHDOG=1`` (e.g. for tests or foreground debugging).
"""
from __future__ import annotations

import os
import sys
import threading
import time

__all__ = ["parent_alive", "install_parent_death_watchdog"]


def parent_alive(ppid: int) -> bool:
    """True if process ``ppid`` is still running. Cross-platform, best-effort."""
    if ppid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_OBJECT_0 = 0x0  # handle signaled == process has exited
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(SYNCHRONIZE, False, ppid)
        if not handle:
            return False  # can't open => already gone
        try:
            # 0 ms wait: WAIT_OBJECT_0 means it exited; WAIT_TIMEOUT means alive.
            return k32.WaitForSingleObject(handle, 0) != WAIT_OBJECT_0
        finally:
            k32.CloseHandle(handle)
    # POSIX
    try:
        os.kill(ppid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal


def install_parent_death_watchdog(poll_seconds: float = 5.0, logger=None) -> bool:
    """Start a daemon thread that hard-exits this process when its parent dies.

    Call once, right before entering the stdio serve loop. Returns True if the
    watchdog was installed, False if skipped (disabled, or no real parent).
    """
    if os.environ.get("APPYHOUR_NO_WATCHDOG"):
        return False
    ppid = os.getppid()
    if ppid <= 0:
        return False

    def _watch() -> None:
        while True:
            time.sleep(poll_seconds)
            try:
                if not parent_alive(ppid):
                    if logger is not None:
                        logger.warning(
                            "parent PID %s is gone — exiting orphaned stdio server", ppid
                        )
                    os._exit(0)  # hard exit: release DB handle + file locks now
            except Exception:
                # The watchdog must NEVER take down a healthy server via its own bug.
                pass

    threading.Thread(target=_watch, name="parent-death-watchdog", daemon=True).start()
    return True
