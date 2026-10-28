"""What the room's sensors add to a look photo: the conditions it was taken in.

The readings at capture time, whether the light just changed (the lamp was switched or the
illuminance jumped) and whether the room is too dark to see. They are recorded with the
photo, told to the vision model (a change of light is not a change of the thing watched),
and given to MEMO as evidence when it speaks (CO₂ climbing means someone is probably here).

Sensor changes that start look tasks by themselves are memo.infrastructure.vision
.start_triggered; the triggers come from the reading projection and the lamp.
"""

from datetime import timedelta

from sqlalchemy import select

from memo.domain.vision import DARK_LUX, light_jump
from memo.infrastructure.database import LampStateRow, ReadingRow

METRICS = ("illuminance_lux", "co2_ppm", "temperature_c", "humidity_pct")
# The light "just changed" when the lamp was switched or the illuminance jumped this recently.
LIGHT_WINDOW = timedelta(seconds=90)
CO2_WINDOW = timedelta(minutes=10)
# A CO₂ trend needs readings spread over at least this long.
CO2_SPAN = timedelta(minutes=5)
SLACK = timedelta(seconds=5)


def conditions(session, device_id, at) -> dict:
    """The room around ``at``; keys are left out when there is nothing to say."""
    rows = session.scalars(
        select(ReadingRow)
        .where(
            ReadingRow.device_id == device_id,
            ReadingRow.quality == "ok",
            ReadingRow.observed_at >= at - CO2_WINDOW,
            ReadingRow.observed_at <= at + SLACK,
        )
        .order_by(ReadingRow.observed_at)
    ).all()
    found = {}
    for key in METRICS:
        values = [row for row in rows if getattr(row, key) is not None]
        if values:
            found[key] = round(getattr(values[-1], key), 1)
    recent = [
        row.illuminance_lux
        for row in rows
        if row.illuminance_lux is not None and row.observed_at >= at - LIGHT_WINDOW
    ]
    jumped = bool(recent) and light_jump(min(recent), max(recent))
    if jumped:
        found["illuminance_before"] = round(recent[0], 1)
    co2 = [row for row in rows if row.co2_ppm is not None]
    if co2 and co2[-1].observed_at - co2[0].observed_at >= CO2_SPAN:
        found["co2_change_10min"] = round(co2[-1].co2_ppm - co2[0].co2_ppm)
    lamp = session.get(LampStateRow, device_id)
    switched = lamp is not None and at - LIGHT_WINDOW <= lamp.updated_at <= at + SLACK
    if switched:
        found["lamp_switched"] = True
    found["light_changed"] = jumped or switched
    if "illuminance_lux" in found:
        found["dark"] = found["illuminance_lux"] < DARK_LUX
    return found


def model_hint(found: dict) -> str:
    """What the vision model should know about the light before comparing photos."""
    hints = []
    if found.get("light_changed"):
        before, now = found.get("illuminance_before"), found.get("illuminance_lux")
        how = f"（照度從 {before:g} 變成 {now:g} lux）" if None not in (before, now) else ""
        hints.append(
            f"感測器：拍照前一分多鐘內光線有明顯變化{how}。"
            "光線、陰影與顏色的差異不算變化，只看東西本身有沒有被移動、拿走或放上。"
        )
    if found.get("dark"):
        hints.append(
            f"感測器：現在照度只有 {found['illuminance_lux']:g} lux，房間很暗；"
            "看不清楚就答 unknown，不要猜。"
        )
    return "".join(hints)


def evidence(found: dict | None) -> dict:
    """The conditions MEMO may mention when it speaks: readings and light changes only."""
    found = found or {}
    keys = (*METRICS, "co2_change_10min", "light_changed")
    return {key: found[key] for key in keys if found.get(key) not in (None, False)}
