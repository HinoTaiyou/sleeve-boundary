import unittest

from sleeve.suggest import (
    accumulation_note,
    decisive_hour,
    outing_hours,
    suggest_clothing,
    suggest_outer,
    suggest_period_outer,
    sunlight_clothing,
    threshold_c,
)


class ThresholdTest(unittest.TestCase):
    def test_offsets_from_18(self):
        self.assertEqual(threshold_c("cold"), 20)
        self.assertEqual(threshold_c("normal"), 18)
        self.assertEqual(threshold_c("hot"), 16)
        self.assertEqual(threshold_c(None), 18)

    def test_below_threshold_is_long_and_at_threshold_is_short(self):
        self.assertEqual(suggest_clothing(19.9, "cold"), "long")
        self.assertEqual(suggest_clothing(20, "cold"), "short")
        self.assertEqual(suggest_clothing(17.9, "normal"), "long")
        self.assertEqual(suggest_clothing(18, "normal"), "short")
        self.assertEqual(suggest_clothing(15.9, "hot"), "long")
        self.assertEqual(suggest_clothing(16, "hot"), "short")
        self.assertEqual(suggest_clothing(17.9, None), "long")

    def test_commute_changes_how_much_wind_matters(self):
        self.assertEqual(suggest_clothing(20, "normal", 5, "walk"), "long")
        self.assertEqual(suggest_clothing(20, "normal", 5, "car"), "short")
        self.assertEqual(suggest_clothing(20, "normal", 5, "transit"), "short")
        self.assertEqual(suggest_clothing(20, "normal", 7, "transit"), "long")


class DayForecastTest(unittest.TestCase):
    def test_a_cold_hour_during_the_day_means_long_sleeves(self):
        hours = [
            {"hour": 9, "temp": 14, "wind": 1, "humidity": 50},
            {"hour": 12, "temp": 21, "wind": 1, "humidity": 50},
            {"hour": 15, "temp": 23, "wind": 1, "humidity": 50},
        ]
        day = decisive_hour(outing_hours(hours, "day"), None)
        midday = decisive_hour(outing_hours(hours, "midday"), None)
        self.assertEqual(day["hour"], 9)
        self.assertEqual(suggest_clothing(day["temp"], "normal", day["wind"], None), "long")
        self.assertEqual(midday["hour"], 12)
        self.assertEqual(suggest_clothing(midday["temp"], "normal", midday["wind"], None), "short")

    def test_morning_keeps_only_early_hours(self):
        hours = [
            {"hour": 6, "temp": 11, "wind": 1, "humidity": 50},
            {"hour": 9, "temp": 14, "wind": 1, "humidity": 50},
            {"hour": 15, "temp": 20, "wind": 1, "humidity": 50},
            {"hour": 21, "temp": 14, "wind": 1, "humidity": 50},
        ]
        morning = outing_hours(hours, "morning")
        self.assertEqual([hour["hour"] for hour in morning], [6, 9])
        self.assertEqual(decisive_hour(morning, None)["hour"], 6)

    def test_a_missing_period_falls_back_to_the_day(self):
        hours = [{"hour": 15, "temp": 20, "wind": 0, "humidity": 40}]
        self.assertEqual(outing_hours(hours, "morning")[0]["hour"], 15)


class SunlightTest(unittest.TestCase):
    def test_the_same_temperature_is_warmer_clothing_at_night(self):
        night, night_why = sunlight_clothing("short", 21, 13.5, "雲")
        day, day_why = sunlight_clothing("long", 9, 13.5, "晴天")
        self.assertEqual((night, night_why), ("long", "night"))
        self.assertEqual((day, day_why), ("short", "sun"))

    def test_a_cold_sunny_morning_stays_long(self):
        clothing, why = sunlight_clothing("long", 6, 11.3, "晴天")
        self.assertEqual(clothing, "long")
        self.assertIsNone(why)


class OuterAdviceTest(unittest.TestCase):
    def test_a_very_cold_low_asks_for_a_thick_outer(self):
        advice = suggest_outer(18, 12, 9)
        self.assertEqual(advice.level, "thick")
        self.assertEqual(advice.label, "厚手アウター")

    def test_a_wide_swing_asks_for_a_proper_layer(self):
        advice = suggest_outer(23, 14, 20)
        self.assertEqual(advice.level, "sturdy")
        self.assertIn("9℃差", advice.message)

    def test_a_moderate_swing_asks_for_a_light_layer(self):
        advice = suggest_outer(22, 17, 19)
        self.assertEqual(advice.level, "light")
        self.assertEqual(advice.label, "薄手の上着持参")

    def test_a_cold_period_asks_for_a_jacket(self):
        self.assertEqual(suggest_period_outer(11).level, "sturdy")
        self.assertEqual(suggest_period_outer(10).level, "thick")

    def test_a_mild_period_asks_for_a_light_layer(self):
        self.assertEqual(suggest_period_outer(17).level, "light")

    def test_a_warm_period_needs_no_outer(self):
        self.assertEqual(suggest_period_outer(20).level, "none")

    def test_a_small_swing_needs_no_outer(self):
        advice = suggest_outer(24, 21, 22)
        self.assertEqual(advice.level, "none")
        self.assertIn("上着なしでOK", advice.message)


class AccumulationNoteTest(unittest.TestCase):
    def test_note_switches_at_10(self):
        self.assertEqual(accumulation_note(0, "rules"), "現在は体質設定に基づいた初期ルールで提案中です")
        self.assertEqual(accumulation_note(9, "rules"), "現在は体質設定に基づいた初期ルールで提案中です")
        self.assertEqual(
            accumulation_note(10, "single_class"),
            "両方の服装データが揃うとAIモデルが有効化されます",
        )
        self.assertEqual(accumulation_note(11, "model"), "AI予測（ロジスティック回帰モデル）による提案")


if __name__ == "__main__":
    unittest.main()
