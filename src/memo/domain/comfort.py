"""Apparent ("feels like") temperature from air temperature and relative humidity.

Uses the US National Weather Service heat index: the Rothfusz regression with its documented
low/high humidity adjustments, and the simple formula below the regression's range.
"""

import math


def heat_index_c(temperature_c: float, humidity_pct: float) -> float:
    t = temperature_c * 9 / 5 + 32
    rh = humidity_pct
    index = 0.5 * (t + 61 + (t - 68) * 1.2 + rh * 0.094)
    if (index + t) / 2 >= 80:
        index = (
            -42.379
            + 2.04901523 * t
            + 10.14333127 * rh
            - 0.22475541 * t * rh
            - 0.00683783 * t * t
            - 0.05481717 * rh * rh
            + 0.00122874 * t * t * rh
            + 0.00085282 * t * rh * rh
            - 0.00000199 * t * t * rh * rh
        )
        if rh < 13 and 80 <= t <= 112:
            index -= (13 - rh) / 4 * math.sqrt((17 - abs(t - 95)) / 17)
        elif rh > 85 and 80 <= t <= 87:
            index += (rh - 85) / 10 * (87 - t) / 5
    return (index - 32) * 5 / 9


def feels_like_c(temperature_c: float | None, humidity_pct: float | None) -> float | None:
    """Never below the air temperature; without humidity it is the air temperature."""
    if temperature_c is None:
        return None
    if humidity_pct is None:
        return temperature_c
    return round(max(temperature_c, heat_index_c(temperature_c, humidity_pct)), 1)
