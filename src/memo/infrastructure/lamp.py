"""Desk lamp state, switched from the web page. MEMO reads it but cannot change it."""

from datetime import datetime, timezone

from memo.domain.lamp import DEFAULT_MODE, LABELS, resolve
from memo.infrastructure.database import LampStateRow


def lamp_output(row: LampStateRow | None) -> dict:
    mode = row.mode if row else DEFAULT_MODE
    return {
        "mode": mode,
        "label": LABELS[mode],
        "last_on": row.last_on if row else None,
        "changed_by": row.changed_by if row else None,
        "updated_at": row.updated_at if row else None,
    }


class LampRepository:
    def __init__(self, session):
        self.session = session

    def get(self, device_id) -> dict:
        return lamp_output(self.session.get(LampStateRow, device_id))

    def set(self, device_id, request: str) -> dict:
        """Apply an absolute or relative request from the web page; returns the new state."""
        row = self.session.get(LampStateRow, device_id)
        current = row.mode if row else DEFAULT_MODE
        mode = resolve(request, current, row.last_on if row else None)
        if row is None:
            row = LampStateRow(device_id=device_id)
            self.session.add(row)
        row.mode, row.changed_by = mode, "api"
        row.updated_at = datetime.now(timezone.utc)
        if mode != "off":
            row.last_on = mode
        self.session.commit()
        return lamp_output(row)
