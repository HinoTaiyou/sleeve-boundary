import unittest

from sleeve.model import (
    boundary_segment,
    predict_clothing,
    suggest_for_today,
    train_sleeve_model,
    training_state,
)


def _row(temp, clothing, wind=2.0, humidity=50.0):
    return {
        "temp_max": temp,
        "wind_speed": wind,
        "humidity": humidity,
        "clothing": clothing,
    }


def _mixed_rows():
    rows = [_row(temp, "long") for temp in (10, 12, 14, 16, 18)]
    rows.extend(_row(temp, "short") for temp in (22, 24, 26, 28, 30))
    return rows


class TrainingStateTest(unittest.TestCase):
    def test_under_ten_stays_on_rules_even_with_both_classes(self):
        rows = _mixed_rows()[:9]
        state = training_state(rows)
        self.assertEqual(state.mode, "rules")
        self.assertIsNone(state.model)

    def test_ten_rows_of_one_class_fall_back(self):
        rows = [_row(temp, "long") for temp in range(10, 20)]
        state = training_state(rows)
        self.assertEqual(state.mode, "single_class")
        self.assertIsNone(state.model)

    def test_ten_rows_without_clothing_fall_back(self):
        rows = [_row(temp, None) for temp in range(10, 20)]
        state = training_state(rows)
        self.assertEqual(state.mode, "single_class")

    def test_both_classes_train_and_predict_long_as_one(self):
        rows = _mixed_rows()
        state = training_state(rows)
        self.assertEqual(state.mode, "model")
        self.assertEqual(predict_clothing(state.model, 12, 2, 50), "long")
        self.assertEqual(predict_clothing(state.model, 28, 2, 50), "short")

        today = _row(12, None)
        clothing, again = suggest_for_today(rows, today, "hot")
        self.assertEqual(again.mode, "model")
        self.assertEqual(clothing, "long")

    def test_single_class_suggestion_uses_constitution_rule(self):
        rows = [_row(temp, "long") for temp in range(10, 20)]
        today = _row(21, None)
        clothing, state = suggest_for_today(rows, today, "cold")
        self.assertEqual(state.mode, "single_class")
        self.assertEqual(clothing, "short")


class BoundaryTest(unittest.TestCase):
    def test_boundary_sits_between_the_two_temperature_groups(self):
        model = train_sleeve_model(_mixed_rows())
        temps, winds = boundary_segment(model, [10, 12, 14, 16, 18, 22, 24, 26, 28, 30], [2, 2])
        self.assertEqual(len(temps), 2)
        self.assertTrue(all(18 < temp < 22 for temp in temps))
        self.assertEqual(len(winds), 2)


if __name__ == "__main__":
    unittest.main()
