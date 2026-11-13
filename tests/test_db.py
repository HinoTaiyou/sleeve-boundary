import tempfile
import unittest
from pathlib import Path

from sleeve.db import (
    connect,
    count_logs,
    get_constitution,
    get_log,
    get_profile,
    init_db,
    list_logs,
    profile_ready,
    save_constitution,
    save_feedback,
    save_profile,
    save_weather,
)


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.dir.name) / "sleeve.db")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def test_weather_upsert_keeps_clothing_and_sensation(self):
        save_weather(
            self.conn,
            log_date="2026-11-05",
            temp_max=20,
            wind_speed=2,
            humidity=50,
            city_name="東京",
        )
        save_feedback(
            self.conn,
            log_date="2026-11-05",
            clothing="long",
            sensation="ok",
            outer=1,
            feel_period="morning_night",
        )
        save_weather(
            self.conn,
            log_date="2026-11-05",
            temp_max=21.5,
            wind_speed=3,
            humidity=55,
            city_name="東京",
        )

        row = get_log(self.conn, "2026-11-05")
        self.assertEqual(row["temp_max"], 21.5)
        self.assertEqual(row["wind_speed"], 3)
        self.assertEqual(row["humidity"], 55)
        self.assertEqual(row["clothing"], "long")
        self.assertEqual(row["sensation"], "ok")
        self.assertEqual(row["sensation_value"], 0)
        self.assertEqual(row["outer"], 1)
        self.assertEqual(row["feel_period"], "morning_night")

    def test_saving_today_does_not_rewrite_older_days(self):
        save_weather(
            self.conn,
            log_date="2026-11-05",
            temp_max=16.2,
            wind_speed=3.8,
            humidity=70,
            city_name="江東区",
            decision_temp=16.2,
        )
        save_feedback(self.conn, log_date="2026-11-05", clothing="long", sensation="cold", outer=1)
        save_weather(
            self.conn,
            log_date="2026-11-06",
            temp_max=23.9,
            wind_speed=3.1,
            humidity=96,
            city_name="新宿区",
            decision_temp=15.6,
        )

        older = get_log(self.conn, "2026-11-05")
        self.assertEqual(older["city_name"], "江東区")
        self.assertEqual(older["temp_max"], 16.2)
        self.assertEqual(older["wind_speed"], 3.8)
        self.assertEqual(older["humidity"], 70)
        self.assertEqual(older["decision_temp"], 16.2)
        self.assertEqual(older["clothing"], "long")
        today = get_log(self.conn, "2026-11-06")
        self.assertEqual(today["city_name"], "新宿区")
        self.assertEqual(today["temp_max"], 23.9)

    def test_feedback_stores_sensation_score_and_outer(self):
        save_weather(
            self.conn,
            log_date="2026-11-05",
            temp_max=16,
            wind_speed=1,
            humidity=40,
            city_name="東京",
        )
        save_feedback(
            self.conn,
            log_date="2026-11-05",
            clothing="long",
            sensation="cold",
            outer=2,
            feel_period="midday",
        )
        row = get_log(self.conn, "2026-11-05")
        self.assertEqual(row["sensation_value"], 1)
        self.assertEqual(row["outer"], 2)
        self.assertEqual(row["feel_period"], "midday")
        with self.assertRaises(ValueError):
            save_feedback(self.conn, log_date="2026-11-05", clothing="long", sensation="hot", outer=3)

    def test_feedback_requires_a_weather_row(self):
        with self.assertRaises(LookupError):
            save_feedback(self.conn, log_date="2026-11-05", clothing="short", sensation="hot")

    def test_constitution_and_history(self):
        self.assertIsNone(get_constitution(self.conn))
        save_constitution(self.conn, "cold")
        save_constitution(self.conn, "hot")
        self.assertEqual(get_constitution(self.conn), "hot")

        save_weather(
            self.conn,
            log_date="2026-11-04",
            temp_max=18,
            wind_speed=1,
            humidity=40,
            city_name="東京",
        )
        save_weather(
            self.conn,
            log_date="2026-11-05",
            temp_max=22,
            wind_speed=2,
            humidity=45,
            city_name="東京",
        )
        dates = [row["log_date"] for row in list_logs(self.conn)]
        self.assertEqual(dates, ["2026-11-05", "2026-11-04"])

    def test_count_logs_counts_saved_days_once(self):
        self.assertEqual(count_logs(self.conn), 0)
        save_weather(
            self.conn,
            log_date="2026-11-05",
            temp_max=20,
            wind_speed=1,
            humidity=40,
            city_name="東京",
        )
        save_weather(
            self.conn,
            log_date="2026-11-05",
            temp_max=21,
            wind_speed=1,
            humidity=40,
            city_name="東京",
        )
        self.assertEqual(count_logs(self.conn), 1)

    def test_profile_and_decision_temp_roundtrip(self):
        self.assertFalse(profile_ready(self.conn))
        save_constitution(self.conn, "normal")
        self.assertFalse(profile_ready(self.conn))
        save_profile(self.conn, "cold", "walk", "night")
        profile = get_profile(self.conn)
        self.assertEqual(profile["constitution"], "cold")
        self.assertEqual(profile["commute"], "walk")
        self.assertEqual(profile["outing"], "night")
        self.assertTrue(profile_ready(self.conn))

        save_weather(
            self.conn,
            log_date="2026-11-06",
            temp_max=22,
            wind_speed=2,
            humidity=50,
            city_name="新宿区",
            decision_temp=14,
        )
        row = get_log(self.conn, "2026-11-06")
        self.assertEqual(row["temp_max"], 22)
        self.assertEqual(row["decision_temp"], 14)
        init_db(self.conn)


if __name__ == "__main__":
    unittest.main()
