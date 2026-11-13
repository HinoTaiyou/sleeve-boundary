"""OpenWeatherMap から、今日の最高気温・風速・湿度を取る。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

GEO_URL = "https://api.openweathermap.org/geo/1.0/direct"
REVERSE_GEO_URL = "https://api.openweathermap.org/geo/1.0/reverse"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
METEO_URL = "https://api.open-meteo.com/v1/forecast"
IP_LOCATION_URL = "https://ipwho.is/"
DISPLAY_HOURS = (3, 6, 9, 12, 15, 18, 21)


class WeatherError(Exception):
    """画面にそのまま出せる気象取得エラー。"""


@dataclass(frozen=True)
class TodayWeather:
    log_date: str
    temp_max: float
    wind_speed: float
    humidity: float
    city_name: str
    # forecast_max: 今日の最高気温の時刻。forecast_period: 外出時間帯の予報。
    # current_temp: 今日の予報が残っていない。
    source: str
    decision_temp: float
    temp_low: float = 0.0
    condition: str = ""
    now_temp: float | None = None
    hours: tuple = ()


@dataclass(frozen=True)
class ForecastSlot:
    hour: int
    minute: int
    temp: float
    temp_max: float
    wind: float
    humidity: float
    condition: str = ""


def fetch_today(
    api_key: str,
    city: str,
    country: str = "",
    timeout: float = 15,
    period: str = "day",
) -> TodayWeather:
    lat, lon, city_name = _geocode(api_key, city, country, timeout)
    return _fetch_at(api_key, lat, lon, city_name, timeout, period)


def fetch_today_at(
    api_key: str,
    lat: float,
    lon: float,
    timeout: float = 15,
    period: str = "day",
) -> TodayWeather:
    _require_api_key(api_key)
    city_name = _reverse_place_name(api_key, lat, lon, timeout)
    return _fetch_at(api_key, lat, lon, city_name, timeout, period)


def locate_by_ip(timeout: float = 8) -> tuple[float, float] | None:
    """この端末の通信元から、おおよその緯度経度を返す。失敗したら None。"""
    try:
        response = requests.get(IP_LOCATION_URL, timeout=timeout)
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    if not data.get("success"):
        return None
    try:
        return float(data["latitude"]), float(data["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


def place_name(item: dict[str, Any], fallback: str = "") -> str:
    local_names = item.get("local_names") or {}
    return str(local_names.get("ja") or item.get("name") or fallback)


def parse_forecast_today(
    payload: dict[str, Any],
    city_name: str,
    now: datetime | None = None,
    period: str = "day",
) -> TodayWeather | None:
    """今日の3時間予報から、外出時間帯に合わせて判定気温と風速・湿度を選ぶ。"""
    tz = _timezone(payload.get("city"))
    current = now.astimezone(tz) if now else datetime.now(tz)
    today = current.date()
    slots = [slot for item in (payload.get("list") or []) if (slot := _slot_on_date(item, tz, today))]
    if not slots:
        return None

    window, used_window = _window_slots(slots, period)
    coolest = min(window, key=lambda slot: (slot.temp, slot.hour))
    nearest = min(
        slots,
        key=lambda slot: abs((slot.hour * 60 + slot.minute) - (current.hour * 60 + current.minute)),
    )
    source = "forecast_period" if period != "day" and used_window else "forecast_day"
    if period != "day" and not used_window:
        source = "forecast_max"
    return TodayWeather(
        log_date=today.isoformat(),
        temp_max=max(slot.temp_max for slot in slots),
        wind_speed=coolest.wind,
        humidity=coolest.humidity,
        city_name=city_name or _resolved_name(payload.get("city"), ""),
        source=source,
        decision_temp=coolest.temp,
        temp_low=min(slot.temp for slot in slots),
        condition=nearest.condition,
        hours=tuple(_hour_dict(slot) for slot in sorted(slots, key=lambda slot: (slot.hour, slot.minute))),
    )


def merge_hours(existing, extra) -> tuple:
    """同じ時刻は既存の予報を残し、足りない時刻だけを足す。"""
    by_hour = {}
    for hour in extra:
        by_hour[int(hour["hour"])] = hour
    for hour in existing:
        by_hour[int(hour["hour"])] = hour
    return tuple(by_hour[key] for key in sorted(by_hour))


def _with_morning(hours, lat: float, lon: float, timeout: float) -> tuple:
    present = {int(hour["hour"]) for hour in hours}
    if all(hour in present for hour in DISPLAY_HOURS):
        return tuple(hours)
    return merge_hours(hours, _morning_from_meteo(lat, lon, timeout))


def _morning_from_meteo(lat: float, lon: float, timeout: float) -> list[dict]:
    try:
        payload = _get_json(
            METEO_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                "timezone": "auto",
                "past_days": 1,
                "forecast_days": 1,
                "wind_speed_unit": "ms",
            },
            timeout,
        )
    except WeatherError:
        return []
    hourly = payload.get("hourly") or {} if isinstance(payload, dict) else {}
    return parse_morning_hours(hourly, day=date.today().isoformat())


def parse_morning_hours(hourly: dict, day: str | None = None) -> list[dict]:
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    humidities = hourly.get("relative_humidity_2m") or []
    winds = hourly.get("wind_speed_10m") or []
    codes = hourly.get("weather_code") or []
    found = []
    for index, stamp in enumerate(times):
        stamp_text = str(stamp)
        hour = _hour_from_stamp(stamp_text)
        if day is not None and not stamp_text.startswith(day):
            continue
        if hour not in DISPLAY_HOURS or index >= len(temps) or temps[index] is None:
            continue
        found.append(
            {
                "hour": hour,
                "temp": float(temps[index]),
                "temp_max": float(temps[index]),
                "wind": float(winds[index]) if index < len(winds) and winds[index] is not None else 0.0,
                "humidity": float(humidities[index]) if index < len(humidities) and humidities[index] is not None else 0.0,
                "condition": _meteo_condition(codes[index] if index < len(codes) else None),
            }
        )
    return found


def _hour_from_stamp(stamp) -> int | None:
    text = str(stamp or "")
    if len(text) < 13:
        return None
    try:
        return int(text[11:13])
    except ValueError:
        return None


def _meteo_condition(code) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return ""
    if value == 0:
        return "晴天"
    if value == 1:
        return "晴れ"
    if value == 2:
        return "薄い雲"
    if value == 3:
        return "雲"
    if value in (45, 48):
        return "霧"
    if value in (51, 53, 55, 56, 57, 80):
        return "小雨"
    if 61 <= value <= 67 or value in (81, 82):
        return "雨"
    if 71 <= value <= 77 or value == 85 or value == 86:
        return "雪"
    if value >= 95:
        return "雷雨"
    return ""


def _window_slots(slots: list[ForecastSlot], period: str) -> tuple[list[ForecastSlot], bool]:
    if period == "midday":
        window = [slot for slot in slots if 10 <= slot.hour < 16]
    elif period == "night":
        window = [slot for slot in slots if slot.hour >= 18]
    else:
        return list(slots), True
    if not window:
        return list(slots), False
    return window, True


def _hour_dict(slot: ForecastSlot) -> dict[str, float | int | str]:
    return {
        "hour": slot.hour,
        "temp": slot.temp,
        "temp_max": slot.temp_max,
        "wind": slot.wind,
        "humidity": slot.humidity,
        "condition": slot.condition,
    }


def parse_current(payload: dict[str, Any], city_name: str, now: datetime | None = None) -> TodayWeather | None:
    main = payload.get("main") or {}
    wind = payload.get("wind") or {}
    if "temp" not in main or "humidity" not in main or "speed" not in wind:
        return None

    tz = _timezone(payload)
    current = now.astimezone(tz) if now else datetime.now(tz)
    return TodayWeather(
        log_date=current.date().isoformat(),
        temp_max=float(main["temp"]),
        wind_speed=float(wind["speed"]),
        humidity=float(main["humidity"]),
        city_name=city_name or _resolved_name(payload, ""),
        source="current_temp",
        decision_temp=float(main["temp"]),
        temp_low=float(main["temp"]),
        condition=_condition(payload),
        now_temp=float(main["temp"]),
    )


def _fetch_at(
    api_key: str,
    lat: float,
    lon: float,
    city_name: str,
    timeout: float,
    period: str = "day",
) -> TodayWeather:
    _require_api_key(api_key)
    forecast = _get_json(
        FORECAST_URL,
        {"lat": lat, "lon": lon, "units": "metric", "lang": "ja", "appid": api_key},
        timeout,
    )
    parsed = parse_forecast_today(forecast, city_name, period=period)
    current = _current_payload(api_key, lat, lon, timeout)
    if parsed is not None:
        parsed = replace(parsed, hours=_with_morning(parsed.hours, lat, lon, timeout))
        return _with_current(parsed, current)

    fallback = parse_current(current, city_name) if current else None
    if fallback is None:
        raise WeatherError("今日の気象を取得できませんでした。時間をおいて再度お試しください。")
    return fallback


def _current_payload(api_key: str, lat: float, lon: float, timeout: float) -> dict[str, Any] | None:
    try:
        payload = _get_json(
            CURRENT_URL,
            {"lat": lat, "lon": lon, "units": "metric", "lang": "ja", "appid": api_key},
            timeout,
        )
    except WeatherError:
        return None
    return payload if isinstance(payload, dict) else None


def _with_current(parsed: TodayWeather, current: dict[str, Any] | None) -> TodayWeather:
    if not current:
        return parsed
    observed = parse_current(current, parsed.city_name)
    if observed is None:
        return parsed
    return replace(
        parsed,
        now_temp=observed.now_temp,
        condition=observed.condition or parsed.condition,
    )


def _geocode(api_key: str, city: str, country: str, timeout: float) -> tuple[float, float, str]:
    _require_api_key(api_key)
    query = f"{city},{country}" if country else city
    data = _get_json(GEO_URL, {"q": query, "limit": 1, "appid": api_key}, timeout)
    if not isinstance(data, list) or not data:
        raise WeatherError(f"都市が見つかりません: {query}")
    item = data[0]
    try:
        return float(item["lat"]), float(item["lon"]), place_name(item, city)
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError(f"都市の位置情報を読み取れません: {query}") from exc


def _reverse_place_name(api_key: str, lat: float, lon: float, timeout: float) -> str:
    try:
        data = _get_json(
            REVERSE_GEO_URL,
            {"lat": lat, "lon": lon, "limit": 1, "appid": api_key},
            timeout,
        )
    except WeatherError:
        return "現在地"
    if isinstance(data, list) and data:
        return place_name(data[0], "現在地")
    return "現在地"


def _require_api_key(api_key: str) -> None:
    if not api_key.strip():
        raise WeatherError("OPENWEATHER_API_KEY が未設定です。.env にキーを入れてください。")


def _get_json(url: str, params: dict[str, Any], timeout: float) -> Any:
    try:
        response = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise WeatherError("気象APIに接続できませんでした。通信環境を確認してください。") from exc

    if response.status_code == 401:
        raise WeatherError("APIキーが無効です。OpenWeatherMap のキーを確認してください。")
    if response.status_code == 404:
        raise WeatherError("都市が見つかりません。OPENWEATHER_CITY を確認してください。")
    if response.status_code == 429:
        raise WeatherError("APIの呼び出し上限に達しました。しばらく待ってから再度お試しください。")
    if not response.ok:
        raise WeatherError(f"気象APIがエラーを返しました（HTTP {response.status_code}）。")
    try:
        return response.json()
    except ValueError as exc:
        raise WeatherError("気象APIの応答を読み取れませんでした。") from exc


def _timezone(city: dict[str, Any] | None):
    offset = int((city or {}).get("timezone") or 0)
    return timezone(timedelta(seconds=offset))


def _condition(payload: dict[str, Any]) -> str:
    weather = payload.get("weather") or []
    if not weather:
        return ""
    return str(weather[0].get("description") or "")


def _resolved_name(city: dict[str, Any] | None, fallback: str) -> str:
    name = (city or {}).get("name")
    return str(name or fallback)


def _slot_on_date(item: dict[str, Any], tz, today) -> ForecastSlot | None:
    try:
        moment = datetime.fromtimestamp(int(item["dt"]), tz)
    except (KeyError, TypeError, ValueError, OSError):
        return None
    if moment.date() != today:
        return None
    main = item.get("main") or {}
    wind = item.get("wind") or {}
    try:
        temp_max = float(main["temp_max"])
        temp = float(main["temp"]) if "temp" in main else temp_max
        return ForecastSlot(
            hour=moment.hour,
            minute=moment.minute,
            temp=temp,
            temp_max=temp_max,
            wind=float(wind["speed"]),
            humidity=float(main["humidity"]),
            condition=_condition(item),
        )
    except (KeyError, TypeError, ValueError):
        return None
