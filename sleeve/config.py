"""環境変数からアプリ設定を読む。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    city: str
    country: str
    db_path: Path


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        raw = st.secrets[name]
    except Exception:
        return default
    text = str(raw).strip()
    return text or default


def load_config() -> AppConfig:
    load_dotenv(ROOT / ".env", override=True)
    return AppConfig(
        api_key=_setting("OPENWEATHER_API_KEY"),
        city=_setting("OPENWEATHER_CITY", "Tokyo") or "Tokyo",
        country=_setting("OPENWEATHER_COUNTRY", "JP"),
        db_path=ROOT / "data" / "sleeve.db",
    )
