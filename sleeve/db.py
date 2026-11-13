"""日次の気象と、実際の服装・体感を SQLite に保存する。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CONSTITUTIONS = ("cold", "normal", "hot")
COMMUTES = ("walk", "transit", "car")
OUTINGS = ("day", "midday", "night")
CLOTHING = ("long", "short")
SENSATIONS = ("cold", "ok", "hot")
SENSATION_SCORES = {"cold": 1, "ok": 0, "hot": -1}
OUTER_VALUES = (0, 1, 2)
FEEL_PERIODS = ("all", "morning", "night", "midday", "morning_night")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    constitution TEXT NOT NULL CHECK (constitution IN ('cold', 'normal', 'hot')),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_logs (
    log_date TEXT PRIMARY KEY,
    temp_max REAL NOT NULL,
    wind_speed REAL NOT NULL,
    humidity REAL NOT NULL,
    clothing TEXT CHECK (clothing IN ('long', 'short')),
    sensation TEXT CHECK (sensation IN ('cold', 'ok', 'hot')),
    city_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    _add_column(conn, "settings", "commute", "TEXT")
    _add_column(conn, "settings", "outing", "TEXT")
    _add_column(conn, "daily_logs", "decision_temp", "REAL")
    _add_column(conn, "daily_logs", "forecast_json", "TEXT")
    _add_column(conn, "daily_logs", "sensation_value", "INTEGER")
    _add_column(conn, "daily_logs", "outer", "INTEGER")
    _add_column(conn, "daily_logs", "feel_period", "TEXT")
    conn.commit()


def save_constitution(conn: sqlite3.Connection, constitution: str) -> None:
    _require(constitution, CONSTITUTIONS, "体質")
    conn.execute(
        """
        INSERT INTO settings (id, constitution, updated_at)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            constitution = excluded.constitution,
            updated_at = excluded.updated_at
        """,
        (constitution, _now()),
    )
    conn.commit()


def get_constitution(conn: sqlite3.Connection) -> str | None:
    profile = get_profile(conn)
    return None if profile is None else profile["constitution"]


def save_profile(conn: sqlite3.Connection, constitution: str, commute: str, outing: str) -> None:
    _require(constitution, CONSTITUTIONS, "体質")
    _require(commute, COMMUTES, "移動手段")
    _require(outing, OUTINGS, "外出時間帯")
    conn.execute(
        """
        INSERT INTO settings (id, constitution, commute, outing, updated_at)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            constitution = excluded.constitution,
            commute = excluded.commute,
            outing = excluded.outing,
            updated_at = excluded.updated_at
        """,
        (constitution, commute, outing, _now()),
    )
    conn.commit()


def get_profile(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT constitution, commute, outing FROM settings WHERE id = 1"
    ).fetchone()


def profile_ready(conn: sqlite3.Connection) -> bool:
    profile = get_profile(conn)
    if profile is None:
        return False
    return (
        profile["constitution"] in CONSTITUTIONS
        and profile["commute"] in COMMUTES
        and profile["outing"] in OUTINGS
    )


def save_weather(
    conn: sqlite3.Connection,
    *,
    log_date: str,
    temp_max: float,
    wind_speed: float,
    humidity: float,
    city_name: str,
    decision_temp: float | None = None,
    forecast_json: str | None = None,
) -> None:
    """指定した日の行だけを書く。ほかの日の場所や気温は変えない。"""
    now = _now()
    judged = temp_max if decision_temp is None else decision_temp
    conn.execute(
        """
        INSERT INTO daily_logs (
            log_date, temp_max, wind_speed, humidity, city_name, decision_temp, forecast_json,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(log_date) DO UPDATE SET
            temp_max = excluded.temp_max,
            wind_speed = excluded.wind_speed,
            humidity = excluded.humidity,
            city_name = excluded.city_name,
            decision_temp = excluded.decision_temp,
            forecast_json = COALESCE(excluded.forecast_json, daily_logs.forecast_json),
            updated_at = excluded.updated_at
        """,
        (log_date, temp_max, wind_speed, humidity, city_name, judged, forecast_json, now, now),
    )
    conn.commit()


def save_feedback(
    conn: sqlite3.Connection,
    *,
    log_date: str,
    clothing: str,
    sensation: str,
    outer: int = 0,
    feel_period: str = "all",
) -> None:
    _require(clothing, CLOTHING, "服装")
    _require(sensation, SENSATIONS, "体感")
    if outer not in OUTER_VALUES:
        raise ValueError(f"上着の値が不正です: {outer}")
    _require(feel_period, FEEL_PERIODS, "時間帯")
    updated = conn.execute(
        """
        UPDATE daily_logs
        SET clothing = ?, sensation = ?, sensation_value = ?, "outer" = ?, feel_period = ?, updated_at = ?
        WHERE log_date = ?
        """,
        (clothing, sensation, SENSATION_SCORES[sensation], outer, feel_period, _now(), log_date),
    )
    conn.commit()
    if updated.rowcount == 0:
        raise LookupError("先に今日の気象を取得してください。")


def get_log(conn: sqlite3.Connection, log_date: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM daily_logs WHERE log_date = ?", (log_date,)).fetchone()


def count_logs(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM daily_logs").fetchone()
    return int(row["n"])


def list_logs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT log_date, temp_max, decision_temp, wind_speed, humidity, clothing, sensation,
                   sensation_value, "outer", feel_period, city_name
            FROM daily_logs
            ORDER BY log_date DESC
            """
        )
    )


def _add_column(conn: sqlite3.Connection, table: str, name: str, typedef: str) -> None:
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name in have:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typedef}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require(value: str, allowed: tuple[str, ...], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"{label}の値が不正です: {value}")
