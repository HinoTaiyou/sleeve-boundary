"""過去15日分のダミーログを outfit_data.db と、アプリの履歴に入れる。"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from sleeve.db import connect, init_db

DB_PATH = Path(__file__).resolve().parent / "outfit_data.db"
APP_DB_PATH = Path(__file__).resolve().parent / "data" / "sleeve.db"
FEELING_NAMES = {1: "cold", 0: "ok", -1: "hot"}

# 10月下旬から11月上旬の東京を想定した並び。急な乱高下ではなく、寒の戻りと小春日和が続く。
WEATHER = (
    (21.6, 1.4, 58),
    (19.4, 2.9, 63),
    (17.8, 1.6, 67),
    (15.8, 3.2, 72),
    (14.3, 2.1, 75),
    (14.9, 0.7, 71),
    (16.7, 1.2, 66),
    (18.4, 2.6, 60),
    (19.7, 3.6, 55),
    (20.0, 1.3, 52),
    (24.5, 0.8, 46),
    (23.2, 1.5, 49),
    (19.5, 0.9, 56),
    (18.6, 2.8, 64),
    (16.2, 3.8, 70),
)


def outfit_code(temp_max: float, wind_speed: float) -> int:
    """18℃未満、または18〜20℃で風が2.5m/s以上なら長袖。"""
    if temp_max < 18 or (18 <= temp_max <= 20 and wind_speed >= 2.5):
        return 1
    return 0


def feeling_code(temp_max: float, wind_speed: float, outfit: int) -> int:
    """服装と矛盾させない。半袖で寒、長袖で暑、にはしない。"""
    del wind_speed  # 体感は気温と服装だけで揃える。風は服装と羽織り側で見る。
    if outfit == 0:
        return -1 if temp_max >= 23 else 0
    return 1 if temp_max < 17 else 0


def outer_code(temp_max: float, wind_speed: float, outfit: int) -> int:
    """半袖には羽織らない。寒いほど厚く、風で長袖にした日は強い風だけ薄手。"""
    if outfit != 1:
        return 0
    if temp_max < 16:
        return 2
    if temp_max < 18 or wind_speed >= 3.5:
        return 1
    return 0


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            date TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            temp_max REAL NOT NULL,
            wind_speed REAL NOT NULL,
            humidity REAL NOT NULL,
            outfit INTEGER NOT NULL,
            feeling INTEGER NOT NULL,
            outer INTEGER NOT NULL
        )
        """
    )


def seed(conn: sqlite3.Connection, today: date | None = None) -> int:
    ensure_table(conn)
    start = (today or date.today()) - timedelta(days=len(WEATHER))
    rows = []
    for offset, (temp_max, wind_speed, humidity) in enumerate(WEATHER):
        outfit = outfit_code(temp_max, wind_speed)
        rows.append(
            (
                (start + timedelta(days=offset)).isoformat(),
                "江東区",
                temp_max,
                wind_speed,
                humidity,
                outfit,
                feeling_code(temp_max, wind_speed, outfit),
                outer_code(temp_max, wind_speed, outfit),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO logs (
            date, city, temp_max, wind_speed, humidity, outfit, feeling, outer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def seed_app(today: date | None = None) -> int:
    """アプリの履歴へ入れる。既存の日は気温も服装も変えず、都市が東京の行だけ江東区に揃える。"""
    start = (today or date.today()) - timedelta(days=len(WEATHER))
    conn = connect(APP_DB_PATH)
    try:
        init_db(conn)
        count = 0
        for offset, (temp_max, wind_speed, humidity) in enumerate(WEATHER):
            log_date = (start + timedelta(days=offset)).isoformat()
            outfit = outfit_code(temp_max, wind_speed)
            feeling = feeling_code(temp_max, wind_speed, outfit)
            stamp = f"{log_date}T03:00:00+00:00"
            updated = conn.execute(
                """
                INSERT INTO daily_logs (
                    log_date, temp_max, wind_speed, humidity, clothing, sensation,
                    sensation_value, "outer", city_name, decision_temp, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(log_date) DO UPDATE SET
                    city_name = excluded.city_name
                WHERE daily_logs.city_name = '東京'
                """,
                (
                    log_date,
                    temp_max,
                    wind_speed,
                    humidity,
                    "long" if outfit == 1 else "short",
                    FEELING_NAMES[feeling],
                    feeling,
                    outer_code(temp_max, wind_speed, outfit),
                    "江東区",
                    temp_max,
                    stamp,
                    stamp,
                ),
            )
            count += updated.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def main() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        count = seed(conn)
    seed_app()
    print(f"{count}件のダミーデータを SQLite に挿入しました！")


if __name__ == "__main__":
    main()
