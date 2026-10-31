"""
Phase 2 parallel store (SQLite).

Intentionally NOT wired into generate / jobs — filesystem remains source of truth.
"""

from darsamooz.store.sqlite import (
    connect,
    count_sections,
    default_db_path,
    sync_from_filesystem,
)

__all__ = [
    "connect",
    "count_sections",
    "default_db_path",
    "sync_from_filesystem",
]
