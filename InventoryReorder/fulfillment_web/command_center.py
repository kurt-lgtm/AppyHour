"""Shim — re-exports cc.engine from standalone CommandCenter project.

Canonical engine now lives at:
    C:\\Users\\Work\\Claude Projects\\CommandCenter\\cc\\engine.py

DB path unchanged (~/.cc/command_center.db) so both apps still share state.
Original engine code backed up at command_center.py.bak.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CC_DIR = Path(r"C:\Users\Work\Claude Projects\CommandCenter")
if str(_CC_DIR) not in sys.path:
    sys.path.insert(0, str(_CC_DIR))

from cc.engine import *  # noqa: F401,F403,E402
from cc import engine as _engine  # noqa: E402

# Re-export every public attr so callers accessing by attribute name work.
for _name in dir(_engine):
    if not _name.startswith("_"):
        globals().setdefault(_name, getattr(_engine, _name))
