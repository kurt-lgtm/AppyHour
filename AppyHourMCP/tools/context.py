"""
Context & Memory MCP resources — exposes Claude Code memory files
and project context as readable MCP resources.
"""

from pathlib import Path

MEMORY_DIR = Path.home() / ".claude" / "projects" / "C--Users-Work" / "memory"


def _make_reader(path: Path):
    """Create a no-arg closure that reads a specific file."""
    def reader() -> str:
        return path.read_text(encoding="utf-8")
    reader.__name__ = f"memory_{path.stem.replace('-', '_')}"
    reader.__doc__ = f"Memory file: {path.name}"
    return reader


def register(mcp):
    """Register context/memory resources on the MCP server."""

    @mcp.resource("context://memory/index")
    def memory_index() -> str:
        """Master index of all memory files — read this first to discover available context."""
        index_path = MEMORY_DIR / "MEMORY.md"
        if not index_path.exists():
            return "# Memory Index\n\nNo memories found."
        return index_path.read_text(encoding="utf-8")

    # Register each .md file (except MEMORY.md) as its own resource
    for md_file in sorted(MEMORY_DIR.glob("*.md")):
        if md_file.name == "MEMORY.md":
            continue
        reader = _make_reader(md_file)
        mcp.resource(f"context://memory/{md_file.stem}")(reader)
