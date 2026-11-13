"""体質と最高気温から、長袖か半袖かを決める。10件未満の初期ルール。"""

from __future__ import annotations

from dataclasses import dataclass

BASE_TEMP_C = 18.0
CONSTITUTION_OFFSET_C = {
    "cold": 2.0,
    "normal": 0.0,
    "hot": -2.0,
}
MODEL_READY_COUNT = 10
# 風速 1 m/s あたり、体感気温を何度下げるか。
WIND_C_PER_MS = {
    "walk": 0.8,
    "transit": 0.3,
    "car": 0.0,
}


def threshold_c(constitution: str | None) -> float:
    offset = CONSTITUTION_OFFSET_C.get(constitution or "normal", 0.0)
    return BASE_TEMP_C + offset


def feels_like(temp_c: float, wind_speed: float, commute: str | None) -> float:
    """移動手段に応じて風で体感気温を下げる。移動手段がなければ気温をそのまま返す。"""
    if commute is None:
        return temp_c
    weight = WIND_C_PER_MS.get(commute, 0.0)
    return temp_c - weight * max(wind_speed, 0.0)


@dataclass(frozen=True)
class OuterAdvice:
    """表示用の上着提案。保存や学習には使わない。"""

    level: str
    label: str
    message: str


def suggest_outer(
    temp_max: float,
    temp_min: float,
    night_temp: float | None = None,
) -> OuterAdvice:
    """一日の気温差を主軸に、上着を4段階で提案する。"""
    cool = temp_min if night_temp is None else min(temp_min, night_temp)
    diff = temp_max - cool
    if cool <= 10:
        return OuterAdvice("thick", "厚手アウター", "夜間にかけて冷え込むため、厚手のアウターが必要です")
    if diff >= 8:
        gap = _diff_text(diff)
        return OuterAdvice("sturdy", "しっかりめの上着", f"昼夜の寒暖差（{gap}℃差）が大きいため、ジャケットやパーカーが必要です")
    if diff >= 4:
        return OuterAdvice("light", "薄手の上着持参", "朝晩に少し冷えるため、脱ぎ着できる薄手の上着（シャツ等）がおすすめです")
    return OuterAdvice("none", "上着不要", "1日を通して気温差が少ないため、上着なしでOKです")


def _diff_text(diff: float) -> str:
    rounded = round(diff, 1)
    if abs(rounded - round(rounded)) < 0.05:
        return str(int(round(rounded)))
    return f"{rounded:.1f}"


def suggest_clothing(
    temp_max: float,
    constitution: str | None,
    wind_speed: float = 0.0,
    commute: str | None = None,
) -> str:
    """体感が閾値未満なら長袖、以上なら半袖。体質が未登録なら普通（18℃）で判定する。"""
    if feels_like(temp_max, wind_speed, commute) < threshold_c(constitution):
        return "long"
    return "short"


def sunlight_clothing(clothing: str, hour: int, temp_c: float, condition: str) -> tuple[str, str | None]:
    """夜は日差しがないので暖かめ、日中の晴れは半袖寄りにする。"""
    sunny = "晴" in (condition or "")
    night = hour >= 18 or hour < 6
    if night and clothing == "short" and temp_c < 18:
        return "long", "night"
    if not night and sunny and clothing == "long" and temp_c >= 13:
        return "short", "sun"
    return clothing, None


def outing_hours(hours, outing: str | None) -> list:
    """外出時間に入る予報だけを返す。その時間帯がなければ、今日残っている予報を返す。"""
    items = list(hours or [])
    if outing == "morning":
        window = [hour for hour in items if int(hour["hour"]) < 10]
    elif outing == "midday":
        window = [hour for hour in items if 10 <= int(hour["hour"]) < 16]
    elif outing == "night":
        window = [hour for hour in items if int(hour["hour"]) >= 18]
    else:
        window = items
    return window or items


def suggest_period_outer(low: float) -> OuterAdvice:
    """選んだ時間帯のいちばん低い気温で上着を決める。一日の寒暖差は見ない。"""
    if low <= 10:
        return OuterAdvice("thick", "厚手アウター", "この時間はかなり冷えるため、厚手のアウターが必要です")
    if low <= 15:
        return OuterAdvice("sturdy", "しっかりめの上着", "この時間は冷えるため、ジャケットやパーカーが必要です")
    if low <= 18:
        return OuterAdvice("light", "薄手の上着持参", "この時間は少し冷えるため、脱ぎ着できる薄手の上着がおすすめです")
    return OuterAdvice("none", "上着不要", "この時間は暖かいため、上着なしで大丈夫です")


def decisive_hour(hours, commute: str | None):
    """一日の予報のうち、体感がいちばん低くなる時間。服装はこの時間で決める。"""
    return min(
        hours,
        key=lambda hour: (feels_like(float(hour["temp"]), float(hour["wind"]), commute), int(hour["hour"])),
    )


def judgment_temperature(row) -> float:
    keys = row.keys()
    if "decision_temp" in keys and row["decision_temp"] is not None:
        return float(row["decision_temp"])
    return float(row["temp_max"])


def accumulation_note(count: int, mode: str = "rules") -> str:
    if count < MODEL_READY_COUNT or mode == "rules":
        return "現在は体質設定に基づいた初期ルールで提案中です"
    if mode == "single_class":
        return "両方の服装データが揃うとAIモデルが有効化されます"
    if mode == "model":
        return "AI予測（ロジスティック回帰モデル）による提案"
    return "現在は体質設定に基づいた初期ルールで提案中です"
