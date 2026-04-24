"""
Writes auditable events into the main auth activity_logs table (users.db).

This is the canonical audit trail for the desktop app.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from auth import get_connection, UserManager


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log_action(
    *,
    user_id: Optional[int],
    action: str,
    target_type: str,
    target_id: Optional[int],
    detail: dict[str, Any] | str | None = None,
    event_type: str = "AUDIT",
) -> None:
    """
    Best-effort logging. Never throws.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        username = str(row[0] if row else "system")
        payload: dict[str, Any] = {
            "action": str(action),
            "target_type": str(target_type),
            "target_id": target_id,
        }
        if isinstance(detail, dict):
            payload["detail"] = detail
        elif isinstance(detail, str) and detail.strip():
            payload["detail"] = {"text": detail.strip()}
        UserManager.add_activity_event(
            username=username,
            event_type=str(event_type or "AUDIT"),
            metadata=payload,
            action_time=_utc_now_iso(),
            action_text=str(action),
        )
    except Exception:
        return

