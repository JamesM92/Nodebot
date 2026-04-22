#!/usr/bin/python3

import sqlite3
from time import strftime, localtime
from commands import register

DATABASE = "/var/lib/weewx/weewx.sdb"

DATA_POINTS = [
    "dateTime","usUnits","interval","appTemp","cloudbase","dewpoint",
    "heatindex","humidex","lightning_distance","lightning_strike_count",
    "maxSolarRad","outHumidity","outTemp","rain","rainRate","windchill",
    "windGust","windrun","windSpeed","windDir"
]

DIRECTIONS = [
    "N","NNE","NE","ENE","E","ESE","SE","SSE",
    "S","SSW","SW","WSW","W","WNW","NW","NNW"
]


def fmt(val, fmtstr, unit=""):
    """Safely format numeric values."""
    try:
        if val is None:
            return "NA"
        return f"{fmtstr.format(val)}{unit}"
    except Exception:
        return "NA"


def render_line(label, value, width=18):
    """Align label/value output."""
    return f"  {label:<{width}} {value}"


def wind_dir(deg):
    """Convert wind direction degrees to cardinal."""
    try:
        if deg is None:
            return "NA"
        card = int((deg + 11.25) / 22.5) % 16
        return f"{deg:.2f}° {DIRECTIONS[card]}"
    except Exception:
        return "NA"


def trend(curr, prev, threshold=0.01):
    """Return trend arrow between two values."""
    try:
        if curr is None or prev is None:
            return ""

        diff = curr - prev

        if abs(diff) < threshold:
            return " →"
        elif diff > 0:
            return " ↑"
        else:
            return " ↓"

    except Exception:
        return ""


def metric(label, field, fmtstr, unit="", show=None, trend_threshold=0.01):
    """Define a metric entry."""
    return {
        "label": label,
        "field": field,
        "fmt": fmtstr,
        "unit": unit,
        "show": show,
        "trend": trend_threshold
    }


SECTIONS = [

("Heat and Humidity", [

    metric("Temperature","outTemp","{:.2f}","°F", trend_threshold=0.2),
    metric("Humidity","outHumidity","\t\t{:.0f}","%RH", trend_threshold=1),
    metric("Heat Index","heatindex","\t{:.2f}","°F"),
    metric("Dew Point","dewpoint","\t{:.2f}","°F")

]),


("Wind and Rain", [

    metric("Wind Speed","windSpeed","{:.2f}"," MPH", trend_threshold=0.5),

    metric(
        "Wind Chill",
        "windchill",
        "{:.2f}",
        "°F",
        show=lambda d: d["windchill"] is not None
    ),

    metric(
        "Rain",
        "rain",
        "{:.2f}",
        " IN",
        show=lambda d: d["rain"] and d["rain"] > 0
    )

]),


("Clouds and Lightning", [

    metric("Cloud Base","cloudbase","\t{:.2f}"," ft"),

    metric(
        "Lightning Dist",
        "lightning_distance",
        "{:.2f}",
        " miles",
        show=lambda d: d["lightning_distance"] and d["lightning_distance"] > 0
    ),

    metric(
        "Lightning Strikes",
        "lightning_strike_count",
        "{:.0f}",
        "",
        show=lambda d: d["lightning_strike_count"] and d["lightning_strike_count"] > 0
    )

])
]


def fetch_latest_two():
    """Fetch latest and previous archive rows."""
    try:

        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        sql = f"""
        SELECT {", ".join(DATA_POINTS)}
        FROM archive
        ORDER BY dateTime DESC
        LIMIT 2
        """

        cur.execute(sql)

        rows = cur.fetchall()

        conn.close()

        if not rows:
            return None, None

        current = rows[0]
        previous = rows[1] if len(rows) > 1 else None

        return current, previous

    except Exception:
        return None, None


@register(
    "weather",
    "current weather",
    category="weewx",
    cooldown=300
)
def weather(args):

    data, prev = fetch_latest_two()

    if not data:
        return "Weather data unavailable."

    lines = []

    lines.append("Current Conditions")

    try:
        update = strftime("%Y-%m-%d %H:%M:%S", localtime(data["dateTime"]))
    except Exception:
        update = "NA"

    lines.append(render_line("Last Updated", update))

    for section, fields in SECTIONS:

        section_lines = []

        for f in fields:

            if f["show"] and not f["show"](data):
                continue

            val = data[f["field"]]

            value = fmt(val, f["fmt"], f["unit"])

            arrow = ""

            if prev:
                arrow = trend(val, prev[f["field"]], f["trend"])

            section_lines.append(
                render_line(f["label"], value + arrow)
            )

        if not section_lines:
            continue

        lines.append("")
        lines.append(section)

        if section == "Wind and Rain":
            lines.append(
                render_line("Wind Dir", wind_dir(data["windDir"]))
            )

        lines.extend(section_lines)

    return "\n".join(lines)