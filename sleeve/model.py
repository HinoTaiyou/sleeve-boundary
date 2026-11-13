"""10件以上かつ長袖と半袖が両方あるときだけ、ロジスティック回帰で提案する。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from sleeve.suggest import MODEL_READY_COUNT, judgment_temperature, suggest_clothing

FEATURE_KEYS = ("temp_max", "wind_speed", "humidity")
LONG = "long"
SHORT = "short"


@dataclass(frozen=True)
class SleeveModel:
    """決定境界は coefficients · [気温, 風速, 湿度] + intercept = 0。"""

    coefficients: tuple[float, float, float]
    intercept: float
    humidity_reference: float


@dataclass(frozen=True)
class PredictionState:
    mode: str
    model: SleeveModel | None


def labeled_rows(rows: Sequence[Mapping]) -> list[Mapping]:
    return [row for row in rows if row["clothing"] in (LONG, SHORT)]


def training_state(rows: Sequence[Mapping]) -> PredictionState:
    if len(rows) < MODEL_READY_COUNT:
        return PredictionState("rules", None)
    model = train_sleeve_model(labeled_rows(rows))
    if model is None:
        return PredictionState("single_class", None)
    return PredictionState("model", model)


def train_sleeve_model(rows: Sequence[Mapping]) -> SleeveModel | None:
    classes = {row["clothing"] for row in rows}
    if classes != {LONG, SHORT}:
        return None

    features = [_features(row) for row in rows]
    targets = [1 if row["clothing"] == LONG else 0 for row in rows]
    scaler = StandardScaler()
    try:
        scaled = scaler.fit_transform(features)
        classifier = LogisticRegression(max_iter=1000, solver="lbfgs")
        classifier.fit(scaled, targets)
    except ValueError:
        return None

    coefficients, intercept = _plane_in_original_space(classifier, scaler)
    humidity = sum(row[2] for row in features) / len(features)
    return SleeveModel(coefficients, intercept, humidity)


def predict_clothing(model: SleeveModel, temp_max: float, wind_speed: float, humidity: float) -> str:
    score = (
        model.coefficients[0] * temp_max
        + model.coefficients[1] * wind_speed
        + model.coefficients[2] * humidity
        + model.intercept
    )
    return LONG if score >= 0 else SHORT


def suggest_for_today(
    rows: Sequence[Mapping],
    today: Mapping | None,
    constitution: str | None,
    commute: str | None = None,
) -> tuple[str | None, PredictionState]:
    state = training_state(rows)
    if today is None:
        return None, state
    temp = judgment_temperature(today)
    if state.mode == "model" and state.model is not None:
        clothing = predict_clothing(
            state.model,
            temp,
            float(today["wind_speed"]),
            float(today["humidity"]),
        )
        return clothing, state
    return suggest_clothing(temp, constitution, float(today["wind_speed"]), commute), state


def boundary_segment(
    model: SleeveModel,
    temps: Sequence[float],
    winds: Sequence[float],
) -> tuple[list[float], list[float]]:
    """気温×風速の表示範囲に収まる決定境界。湿度は学習データの平均で固定する。"""
    if not temps or not winds:
        return [], []

    temp_min, temp_max = min(temps), max(temps)
    wind_min, wind_max = min(winds), max(winds)
    temp_pad = max(temp_max - temp_min, 1.0) * 0.12
    wind_pad = max(wind_max - wind_min, 0.5) * 0.12
    left, right = temp_min - temp_pad, temp_max + temp_pad
    bottom, top = wind_min - wind_pad, wind_max + wind_pad
    temp_coef, wind_coef, humidity_coef = model.coefficients
    humidity = model.humidity_reference
    constant = model.intercept + humidity_coef * humidity

    if abs(wind_coef) < 1e-9:
        if abs(temp_coef) < 1e-9:
            return [], []
        temp = -constant / temp_coef
        if left <= temp <= right:
            return [temp, temp], [bottom, top]
        return [], []

    hits: list[tuple[float, float]] = []
    for temp in (left, right):
        wind = -(constant + temp_coef * temp) / wind_coef
        if bottom <= wind <= top:
            hits.append((temp, wind))
    if abs(temp_coef) >= 1e-9:
        for wind in (bottom, top):
            temp = -(constant + wind_coef * wind) / temp_coef
            if left <= temp <= right:
                hits.append((temp, wind))

    unique: list[tuple[float, float]] = []
    for point in hits:
        if not any(abs(point[0] - kept[0]) < 1e-6 and abs(point[1] - kept[1]) < 1e-6 for kept in unique):
            unique.append(point)
    if len(unique) < 2:
        return [], []
    unique.sort()
    start, end = unique[0], unique[-1]
    return [start[0], end[0]], [start[1], end[1]]


def _features(row: Mapping) -> list[float]:
    return [judgment_temperature(row), float(row["wind_speed"]), float(row["humidity"])]


def _plane_in_original_space(classifier, scaler) -> tuple[tuple[float, float, float], float]:
    weights = classifier.coef_[0]
    bias = float(classifier.intercept_[0])
    mean = scaler.mean_
    scale = scaler.scale_
    coefficients = tuple(float(weights[index] / scale[index]) for index in range(3))
    intercept = bias - sum(float(weights[index] * mean[index] / scale[index]) for index in range(3))
    # coef_ は classes_[1] 側。正のスコアが長袖（1）になるよう向きをそろえる。
    if int(classifier.classes_[1]) != 1:
        coefficients = tuple(-value for value in coefficients)
        intercept = -intercept
    return coefficients, intercept
