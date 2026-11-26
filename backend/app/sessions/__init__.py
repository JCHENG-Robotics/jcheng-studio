"""Operator session persistence for the Studio.

The session store is deliberately separate from the legacy console event log
(`app/persistence/store.py`). It lives outside the repository and implements
the record/authorization contract in `docs/SESSIONS.md`.
"""

from .store import SessionStore, SessionStoreError

__all__ = ["SessionStore", "SessionStoreError"]
