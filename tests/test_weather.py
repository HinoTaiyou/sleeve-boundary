import unittest
from datetime import datetime, timedelta, timezone

from sleeve.weather import merge_hours, parse_current, parse_forecast_today, parse_morning_hours, place_name


TZ = timezone(timedelta(hours=9))


class PlaceNameTest(unittest.TestCase):
    def test_prefers_japanese_local_name(self):
        self.assertEqual(
            place_name({"name": "Shibuya", "local_names": {"ja": "渋谷区"}}, "現在地"),
            "渋谷区",
        )
        self.assertEqual(place_name({}, "現在地"), "現在地")


class ForecastParseTest(unittest.TestCase):
    def test_picks_wind_and_humidity_at_the_warmest_hour(self):
        today = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
        payload = {
            "city": {"name": "Tokyo", "timezone": 32400},
            "list": [
                _slot(today.replace(hour=9), temp_max=16.0, wind=4.0, humidity=70),
                _slot(today.replace(hour=15), temp_max=22.5, wind=2.2, humidity=48),
                _slot(today.replace(hour=21), temp_max=18.0, wind=1.0, humidity=80),
                _slot(today + timedelta(days=1), temp_max=30.0, wind=9.0, humidity=10),
            ],
        }

        weather = parse_forecast_today(payload, "東京", now=today)

        self.assertIsNotNone(weather)
        self.assertEqual(weather.log_date, "2026-11-05")
        self.assertEqual(weather.temp_max, 22.5)
        self.assertEqual(weather.temp_low, 16.0)
        self.assertEqual(weather.wind_speed, 4.0)
        self.assertEqual(weather.humidity, 70)
        self.assertEqual(weather.source, "forecast_day")
        self.assertEqual(weather.city_name, "東京")
        self.assertEqual(weather.decision_temp, 16.0)
        self.assertEqual([hour["hour"] for hour in weather.hours], [9, 15, 21])

    def test_midday_uses_the_warmest_slot_between_10_and_16(self):
        today = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
        payload = {
            "city": {"name": "Tokyo", "timezone": 32400},
            "list": [
                _slot(today.replace(hour=9), temp_max=24.0, wind=5.0, humidity=40),
                _slot(today.replace(hour=12), temp_max=19.0, wind=3.0, humidity=50),
                _slot(today.replace(hour=15), temp_max=21.0, wind=1.5, humidity=55),
            ],
        }
        weather = parse_forecast_today(payload, "東京", now=today, period="midday")
        self.assertEqual(weather.temp_max, 24.0)
        self.assertEqual(weather.decision_temp, 19.0)
        self.assertEqual(weather.wind_speed, 3.0)
        self.assertEqual(weather.source, "forecast_period")

    def test_night_uses_the_slot_nearest_21(self):
        today = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
        payload = {
            "city": {"name": "Tokyo", "timezone": 32400},
            "list": [
                _slot(today.replace(hour=15), temp_max=25.0, wind=4.0, humidity=40, temp=24.0),
                _slot(today.replace(hour=18), temp_max=18.0, wind=2.0, humidity=60, temp=17.0),
                _slot(today.replace(hour=21), temp_max=15.0, wind=1.0, humidity=70, temp=14.0),
            ],
        }
        weather = parse_forecast_today(payload, "東京", now=today, period="night")
        self.assertEqual(weather.temp_max, 25.0)
        self.assertEqual(weather.decision_temp, 14.0)
        self.assertEqual(weather.wind_speed, 1.0)
        self.assertEqual(weather.humidity, 70)
        self.assertEqual(weather.source, "forecast_period")

    def test_night_without_an_evening_slot_falls_back_to_the_daily_max(self):
        today = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
        payload = {
            "city": {"name": "Tokyo", "timezone": 32400},
            "list": [
                _slot(today.replace(hour=9), temp_max=16.0, wind=4.0, humidity=70),
                _slot(today.replace(hour=15), temp_max=22.5, wind=2.2, humidity=48),
            ],
        }
        weather = parse_forecast_today(payload, "東京", now=today, period="night")
        self.assertEqual(weather.decision_temp, 16.0)
        self.assertEqual(weather.source, "forecast_max")

    def test_returns_none_when_today_has_no_forecast_slot(self):
        now = datetime(2026, 11, 5, 23, 30, tzinfo=TZ)
        payload = {
            "city": {"name": "Tokyo", "timezone": 32400},
            "list": [_slot(now + timedelta(hours=2), temp_max=12, wind=1, humidity=60)],
        }
        self.assertIsNone(parse_forecast_today(payload, "東京", now=now))


class CurrentParseTest(unittest.TestCase):
    def test_uses_current_observation_as_fallback(self):
        now = datetime(2026, 11, 5, 23, 40, tzinfo=TZ)
        payload = {
            "name": "Tokyo",
            "timezone": 32400,
            "main": {"temp": 14.2, "humidity": 66},
            "wind": {"speed": 3.4},
        }
        weather = parse_current(payload, "東京", now=now)
        self.assertEqual(weather.log_date, "2026-11-05")
        self.assertEqual(weather.temp_max, 14.2)
        self.assertEqual(weather.wind_speed, 3.4)
        self.assertEqual(weather.humidity, 66)
        self.assertEqual(weather.source, "current_temp")


class MorningHoursTest(unittest.TestCase):
    def test_adds_missing_day_hours_without_replacing_later_hours(self):
        existing = [{"hour": 12, "temp": 20.0, "temp_max": 21.0, "wind": 2.0, "humidity": 50, "condition": "薄い雲"}]
        morning = parse_morning_hours(
            {
                "time": [
                    "2026-11-05T06:00",
                    "2026-11-06T03:00",
                    "2026-11-06T06:00",
                    "2026-11-06T09:00",
                    "2026-11-06T12:00",
                ],
                "temperature_2m": [1.0, 11.0, 14.2, 16.8, 1.0],
                "relative_humidity_2m": [1, 60, 80, 70, 10],
                "wind_speed_10m": [1, 1.1, 1.5, 2.4, 9.0],
                "weather_code": [0, 2, 1, 3, 0],
            },
            day="2026-11-06",
        )
        merged = merge_hours(existing, morning)
        self.assertEqual([hour["hour"] for hour in merged], [3, 6, 9, 12])
        self.assertEqual(merged[0]["condition"], "薄い雲")
        self.assertEqual(merged[1]["condition"], "晴れ")
        self.assertEqual(merged[2]["temp"], 16.8)
        self.assertEqual(merged[3]["temp"], 20.0)


def _slot(moment: datetime, temp_max: float, wind: float, humidity: float, temp: float | None = None) -> dict:
    main = {"temp_max": temp_max, "humidity": humidity}
    if temp is not None:
        main["temp"] = temp
    return {
        "dt": int(moment.timestamp()),
        "main": main,
        "wind": {"speed": wind},
    }


if __name__ == "__main__":
    unittest.main()
