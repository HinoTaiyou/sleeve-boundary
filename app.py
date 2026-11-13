"""気象の取得と、服装・体感の保存。"""

from __future__ import annotations

import calendar
import html
import json
from datetime import date, datetime

import streamlit as st

from sleeve.chart import build_scatter
from sleeve.config import load_config
from sleeve.db import (
    connect,
    get_log,
    get_profile,
    init_db,
    list_logs,
    profile_ready,
    save_feedback,
    save_profile,
    save_weather,
)
from sleeve.model import predict_clothing, training_state
from sleeve.suggest import (
    WIND_C_PER_MS,
    accumulation_note,
    decisive_hour,
    feels_like,
    judgment_temperature,
    outing_hours,
    suggest_outer,
    suggest_clothing,
    suggest_period_outer,
    sunlight_clothing,
    threshold_c,
)
from sleeve.weather import WeatherError, fetch_today, fetch_today_at, locate_by_ip, merge_hours
from streamlit_option_menu import option_menu

CONSTITUTION_OPTIONS = (("hot", "暑がり"), ("normal", "普通"), ("cold", "寒がり"))
COMMUTE_OPTIONS = (("walk", "徒歩・自転車"), ("transit", "電車・バス"), ("car", "車・リモート"))
OUTING_OPTIONS = (
    ("day", "朝〜夕方（標準）"),
    ("midday", "昼間メイン"),
    ("night", "夜メイン・夜遅く"),
)
TEMP_NAMES = {"day": "最高気温", "midday": "昼間の気温", "night": "夜間の想定気温"}
CONSTITUTION_LABELS = dict(CONSTITUTION_OPTIONS)
COMMUTE_LABELS = dict(COMMUTE_OPTIONS)
CLOTHING_LABELS = {"long": "長袖", "short": "半袖"}
SENSATION_OPTIONS = ((1, "寒かった"), (0, "ちょうどいい"), (-1, "暑かった"))
SENSATION_CODES = {1: "cold", 0: "ok", -1: "hot"}
SENSATION_LABELS = {"cold": "寒かった", "ok": "ちょうどいい", "hot": "暑かった"}
SENSATION_TONES = {"cold": "pill-cold", "ok": "pill-ok", "hot": "pill-hot"}
OUTER_OPTIONS = ((0, "なし"), (1, "薄手（カーディガン等）"), (2, "厚手（ジャケット等）"))
OUTER_TABLE = {0: "なし", 1: "薄手", 2: "厚手"}
FEEL_PERIOD_OPTIONS = (("all", "一日を通して"), ("morning", "朝"), ("night", "夜"), ("midday", "昼間"))


def main() -> None:
    st.set_page_config(page_title="適正服装診断", layout="centered", initial_sidebar_state="collapsed")
    _styles()

    config = load_config()
    conn = connect(config.db_path)
    init_db(conn)
    today = date.today().isoformat()

    try:
        _topbar(today)
        if not profile_ready(conn):
            _onboarding(conn)
            return

        profile = get_profile(conn)
        waiting_for_location = _prepare_location()
        rows = list_logs(conn)
        state = training_state(rows)
        today_row = get_log(conn, today)
        page = _current_page()
        if page != "Home":
            st.session_state.show_feedback = None
        if page == "Home" and _review_open(today):
            _feedback_screen(conn, today)
        elif page == "Home":
            saved_note = st.session_state.pop("saved_note", None)
            if saved_note:
                _show_saved_note(saved_note)
            if waiting_for_location:
                st.info("いまの位置を確認しています。")
            _render_today(conn, config, today, today_row, state, profile, len(rows))
            if st.session_state.get("show_clothing") == today:
                _reflect_button(today)
        elif page == "境界線":
            st.markdown("<p class='section-title'>長袖と半袖の境界</p>", unsafe_allow_html=True)
            _show_chart(rows, state.model, today_row)
        elif page == "履歴":
            st.markdown("<p class='section-title'>服装履歴</p>", unsafe_allow_html=True)
            _history_section(conn)
        else:
            _settings_page(conn, profile)
        _bottom_nav()
        if waiting_for_location:
            _ask_browser_location(force=True)
        else:
            _ask_browser_location()
            _refresh_weather(conn, config, today, profile)
    finally:
        conn.close()


def _styles() -> None:
    st.markdown(
        """
        <style>
        html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMarkdown"] p {
            font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Avenir Next", "Segoe UI", sans-serif;
        }
        header[data-testid="stHeader"], footer, [data-testid="stToolbar"], #MainMenu { display: none !important; }
        .stApp {
            background: linear-gradient(180deg, #c5e4f7 0px, #f4f8fc 240px, #f4f8fc 100%);
            color: #1c2833;
        }
        [data-testid="stMain"],
        [data-testid="stAppViewContainer"] {
            width: 100% !important;
            max-width: 100% !important;
            margin: 0 !important;
            left: 0 !important;
            right: 0 !important;
        }
        [data-testid="stSidebar"] { display: none !important; }
        .block-container {
            box-sizing: border-box !important;
            width: min(760px, calc(100% - 2rem)) !important;
            max-width: min(760px, calc(100% - 2rem)) !important;
            margin-left: auto !important;
            margin-right: auto !important;
            padding-top: 1.5rem;
            padding-bottom: 0.6rem;
            padding-left: 0 !important;
            padding-right: 0 !important;
            min-height: 100vh;
            display: flex !important;
            flex-direction: column !important;
        }
        .block-container > [data-testid="stVerticalBlock"] {
            flex: 1 1 auto;
            display: flex !important;
            flex-direction: column !important;
            gap: 0.8rem !important;
        }
        [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] {
            background: #ffffff;
            border: 1px solid #e3ebf3 !important;
            border-radius: 24px !important;
            padding: 1.15rem 1.25rem 1.05rem !important;
            box-shadow: 0 10px 28px rgba(22, 50, 74, 0.06);
        }
        [data-testid="stLayoutWrapper"]:has(.hero-long) > [data-testid="stVerticalBlock"] {
            background: linear-gradient(180deg, #e7f4fc 0%, #ffffff 58%);
            border-color: #b9daf2 !important;
        }
        [data-testid="stLayoutWrapper"]:has(.hero-short) > [data-testid="stVerticalBlock"] {
            background: linear-gradient(180deg, #fff1ec 0%, #ffffff 58%);
            border-color: #f3d2c8 !important;
        }
        [data-testid="stLayoutWrapper"]:has(.hero-pending) > [data-testid="stVerticalBlock"] {
            background: linear-gradient(180deg, #f7fbfe 0%, #ffffff 70%);
        }
        iframe[title="streamlit_option_menu.option_menu"] {
            position: relative !important;
            left: auto !important;
            bottom: auto !important;
            transform: none !important;
            width: 100% !important;
            height: 64px !important;
            z-index: 5;
            background: #ffffff;
            border: 1px solid #e3ebf3 !important;
            border-radius: 18px !important;
            box-shadow: 0 8px 24px rgba(22, 50, 74, 0.08);
        }
        div[data-testid="stElementContainer"]:has(iframe[title="streamlit_option_menu.option_menu"]) {
            margin-top: auto !important;
            position: sticky;
            bottom: 0.35rem;
            z-index: 30;
            height: auto !important;
            min-height: 0 !important;
            background: linear-gradient(180deg, rgba(244, 248, 252, 0), #f4f8fc 28%);
            padding-top: 0.8rem;
        }
        .stMarkdown .topbar {
            display: flex; justify-content: space-between; align-items: baseline;
            margin: 0 0 1.35rem;
        }
        .stMarkdown .mark {
            margin: 0 !important;
            font-size: 1.05rem !important;
            letter-spacing: 0.04em;
            font-weight: 700 !important;
            color: #1b7fbf !important;
        }
        .stMarkdown .today {
            margin: 0 !important;
            font-size: 0.92rem !important;
            color: #5c6b7a !important;
        }
        .stMarkdown .hero-title {
            margin: 0 0 0.45rem !important;
            font-size: 2.35rem !important;
            line-height: 1.25 !important;
            font-weight: 700 !important;
            letter-spacing: 0;
            color: #1c2833 !important;
        }
        .stMarkdown .lede, .stMarkdown .q-help, .stMarkdown .section-help {
            margin: 0 !important;
            color: #5c6b7a !important;
            font-size: 0.98rem !important;
            line-height: 1.65 !important;
        }
        .stMarkdown .q-block { margin: 1.35rem 0 0.35rem; }
        .stMarkdown .q-title, .stMarkdown .section-title {
            margin: 0 0 0.28rem !important;
            font-size: 1.12rem !important;
            font-weight: 700 !important;
            color: #1c2833 !important;
            font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Apple Color Emoji", "Segoe UI Emoji", sans-serif !important;
        }
        .stMarkdown .section-title { margin-top: 1.6rem !important; }
        [data-testid="stLayoutWrapper"] .section-title { margin-top: 0 !important; }
        .stMarkdown .place-kicker {
            margin: 0 !important;
            font-size: 0.84rem !important;
            letter-spacing: 0.04em;
            color: #1b7fbf !important;
        }
        .stMarkdown .place-name {
            margin: 0.1rem 0 0.75rem !important;
            font-size: 2.15rem !important;
            font-weight: 700 !important;
            line-height: 1.2 !important;
            color: #1c2833 !important;
        }
        [data-testid="stForm"] {
            border: 0;
            padding: 0;
            background: transparent;
        }
        .feedback-mark, .reflect-mark, .back-mark { display: none; }
        [data-testid="stElementContainer"]:has(.back-mark) {
            height: 0 !important;
            min-height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            overflow: hidden !important;
        }
        [data-testid="stElementContainer"]:has(.back-mark) + [data-testid="stElementContainer"] {
            width: auto !important;
        }
        [data-testid="stElementContainer"]:has(.back-mark) + [data-testid="stElementContainer"] button {
            background: transparent !important;
            color: #1b7fbf !important;
            border: 0 !important;
            min-height: 2.2rem !important;
            width: auto !important;
            padding: 0 0.15rem !important;
            box-shadow: none !important;
        }
        [data-testid="stElementContainer"]:has(.back-mark) + [data-testid="stElementContainer"] button:hover {
            background: transparent !important;
            color: #142c46 !important;
        }
        [data-testid="stElementContainer"]:has(.reflect-mark) {
            height: 0 !important;
            min-height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            overflow: hidden !important;
        }
        [data-testid="stElementContainer"]:has(.reflect-mark) + [data-testid="stElementContainer"] button {
            background: #1b7fbf !important;
            color: #ffffff !important;
            border: 0 !important;
            min-height: 2.8rem !important;
        }
        [data-testid="stElementContainer"]:has(.reflect-mark) + [data-testid="stElementContainer"] button:hover {
            background: #166ea6 !important;
            color: #ffffff !important;
            border: 0 !important;
        }
        .form-spacer { height: 4.5rem; }
        .stMarkdown .field-label {
            margin: 0.7rem 0 0.35rem !important;
            font-size: 0.95rem !important;
            font-weight: 700 !important;
            color: #16324a !important;
        }
        [data-testid="stForm"]:has(.feedback-mark) div[role="radiogroup"] {
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)) !important;
        }
        [data-testid="stForm"]:has(.feedback-mark) [data-testid="stRadioOption"] {
            min-height: 2.55rem;
            justify-content: center;
            padding: 0.4rem 0.7rem;
        }
        [data-testid="stForm"]:has(.feedback-mark) [data-testid="stRadioOption"] p {
            text-align: left !important;
            font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Apple Color Emoji", "Segoe UI Emoji", sans-serif !important;
        }
        [data-testid="stForm"]:has(.feedback-mark) .stFormSubmitButton button {
            min-height: 3.5rem !important;
            margin-top: 1.2rem !important;
            font-size: 1.08rem !important;
        }
        [data-testid="stElementContainer"]:has([data-testid="stRadio"]),
        [data-testid="stElementContainer"]:has(.stFormSubmitButton) {
            width: 100% !important;
        }
        div[data-testid="stRadio"],
        div[data-testid="stRadioGroup"] {
            width: 100% !important;
        }
        div[data-testid="stRadio"] div[role="radiogroup"] {
            display: grid !important;
            width: 100% !important;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 0.55rem !important;
        }
        div[data-testid="stRadioGroup"] > div { min-width: 0; width: auto !important; }
        [data-testid="stRadioOption"] {
            width: 100% !important;
            box-sizing: border-box;
            border: 1px solid #d5e2ee;
            border-radius: 16px;
            background: #ffffff;
            min-height: 4.4rem;
            padding: 0.7rem 0.75rem;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
        }
        [data-testid="stRadioOption"] > div > div:first-child { display: none !important; }
        [data-testid="stRadioOption"] p {
            margin: 0 !important;
            text-align: center;
            font-weight: 650 !important;
            color: #1c2833 !important;
        }
        [data-testid="stRadioOption"][data-selected="true"] {
            border-color: #1b7fbf;
            background: #e7f3fb;
            box-shadow: inset 0 0 0 1px rgba(27, 127, 191, 0.35);
        }
        .stButton > button, .stFormSubmitButton button {
            border-radius: 12px !important;
            min-height: 2.9rem;
            font-weight: 700 !important;
            border: 0 !important;
            background: #1b3a5c !important;
            color: #ffffff !important;
            box-shadow: none !important;
        }
        .stFormSubmitButton button {
            width: 100%;
            margin-top: 0.8rem;
        }
        .stButton > button:hover, .stFormSubmitButton button:hover,
        .stButton > button[data-testid="stBaseButton-primary"]:hover {
            background: #142c46 !important;
            color: #ffffff !important;
        }
        .stButton > button:disabled {
            background: #c5d0dc !important;
            color: #ffffff !important;
        }
        .proposal {
            border-radius: 24px;
            padding: 1.25rem 1.35rem 1.15rem;
            margin: 0 0 0.85rem;
            background: #ffffff;
            border: 1px solid #e3ebf3;
        }
        .proposal-short { background: #ffffff; }
        .proposal-empty { background: #ffffff; }
        .stMarkdown .proposal-kicker {
            margin: 0 !important;
            font-size: 0.84rem !important;
            letter-spacing: 0.04em;
            color: #1b7fbf !important;
        }
        .stMarkdown .proposal-title {
            margin: 0.2rem 0 0.45rem !important;
            font-size: 2.15rem !important;
            font-weight: 700 !important;
            line-height: 1.2 !important;
            color: #1c2833 !important;
        }
        .stMarkdown .proposal-title-empty { font-size: 1.35rem !important; font-weight: 650 !important; }
        .stMarkdown .proposal-detail {
            margin: 0 !important;
            font-size: 0.98rem !important;
            line-height: 1.65 !important;
            color: #3d4d5c !important;
        }
        .stMarkdown .meter { margin: 0.2rem 0 0.4rem; }
        .stMarkdown .meter-row {
            display: flex; justify-content: space-between; align-items: baseline; margin: 0 0 0.4rem;
        }
        .stMarkdown .meter-label { margin: 0 !important; color: #5c6b7a !important; font-size: 0.92rem !important; }
        .stMarkdown .meter-value { margin: 0 !important; color: #1c2833 !important; font-weight: 700 !important; }
        .stMarkdown .meter-track {
            height: 8px; border-radius: 999px; background: #e3ebf3; overflow: hidden;
        }
        .stMarkdown .meter-fill { height: 100%; border-radius: 999px; background: #1b7fbf; }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e3ebf3;
            border-radius: 18px;
            padding: 0.8rem 0.95rem 0.65rem;
        }
        [data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] { color: #5c6b7a !important; }
        div[data-testid="stMetricValue"] { color: #1c2833; font-size: 1.65rem; }
        [data-testid="stExpander"] {
            border: 1px solid #e3ebf3 !important;
            border-radius: 18px;
            background: #ffffff;
            margin-top: 0.6rem;
        }
        [data-testid="stExpander"] summary { color: #1c2833; }
        div[data-testid="stAlert"] {
            border-radius: 16px !important;
            background: #ffffff !important;
            border: 1px solid #e3ebf3 !important;
        }
        div[data-testid="stAlert"] p { color: #1c2833 !important; }
        [data-testid="stCaptionContainer"] { color: #5c6b7a; }
        [data-testid="stDataFrame"] { border: 1px solid #e3ebf3; border-radius: 16px; overflow: hidden; }
        [data-testid="stWidgetLabel"] p { color: #1c2833 !important; }
        .stMarkdown .wx-place {
            margin: 0 !important;
            font-size: 1.85rem !important;
            font-weight: 700 !important;
            line-height: 1.15 !important;
            color: #1c2833 !important;
        }
        .stMarkdown .wx-condition {
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            gap: 0.4rem;
            margin: 0.2rem 0 0 !important;
            color: #5c6b7a !important;
            font-size: 1.15rem !important;
            line-height: 1 !important;
            white-space: nowrap;
        }
        .stMarkdown .wx-icon,
        .stMarkdown .h-icon,
        .stMarkdown .hero-line {
            font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Apple Color Emoji", "Segoe UI Emoji", sans-serif !important;
        }
        .stMarkdown .wx-icon {
            display: inline-flex !important;
            align-items: center;
            justify-content: center;
            width: 1.45rem;
            height: 1.45rem;
            font-size: 1.25rem !important;
            line-height: 1 !important;
            margin: 0 !important;
        }
        .stMarkdown .panel {
            background: #ffffff;
            border: 1px solid #e3ebf3;
            border-radius: 24px;
            padding: 1.15rem 1.3rem 1.2rem;
            box-shadow: 0 10px 28px rgba(22, 50, 74, 0.06);
            margin: 0 0 0.9rem;
        }
        .stMarkdown .wx-layout {
            display: grid;
            grid-template-columns: minmax(10.5rem, 0.72fr) minmax(0, 1.4fr);
            gap: 1.75rem;
            align-items: center;
        }
        .stMarkdown .wx-word { line-height: 1 !important; }
        .stMarkdown .wx-temp {
            margin: 0.15rem 0 0 !important;
            font-size: 4.8rem !important;
            font-weight: 620 !important;
            line-height: 0.9 !important;
            letter-spacing: -0.045em;
            color: #16324a !important;
        }
        .stMarkdown .card-label {
            margin: 0 0 0.75rem !important;
            font-size: 0.82rem !important;
            font-weight: 700 !important;
            letter-spacing: 0.08em;
            color: #1b7fbf !important;
        }
        .stMarkdown .hero-card {
            position: relative;
            text-align: center;
            padding: 0.2rem 0.4rem 0.15rem;
        }
        .stMarkdown .hero-card:has(.hero-status) { padding-top: 1.55rem; }
        .stMarkdown .hero-status {
            position: absolute;
            top: 0;
            right: 0;
            margin: 0 !important;
            padding: 0.18rem 0.55rem;
            border-radius: 999px;
            background: #e7f3fb;
            color: #1b4f8c !important;
            font-size: 0.72rem !important;
            font-weight: 700 !important;
            line-height: 1.4 !important;
            font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Apple Color Emoji", "Segoe UI Emoji", sans-serif !important;
        }
        .stMarkdown .hero-line {
            margin: 0.15rem 0 0.45rem !important;
            font-size: 2.15rem !important;
            font-weight: 760 !important;
            line-height: 1.25 !important;
            letter-spacing: -0.02em;
            color: #16324a !important;
        }
        .stMarkdown .outer-advice { margin: 0.15rem 0 0.35rem !important; }
        .stMarkdown .outer-message {
            margin: 0 auto 0.7rem !important;
            max-width: 36rem;
            color: #3d4d5c !important;
            font-size: 0.95rem !important;
            line-height: 1.6 !important;
        }
        .stMarkdown .hero-long .hero-line { color: #1b6ca8 !important; }
        .stMarkdown .hero-short .hero-line { color: #d4533a !important; }
        .stMarkdown .hero-note {
            margin: 0 !important;
            color: #5c6b7a !important;
            font-size: 0.92rem !important;
        }
        .stMarkdown .hero-detail {
            margin: 0.7rem auto 0 !important;
            max-width: 38rem;
            color: #3d4d5c !important;
            font-size: 0.98rem !important;
            line-height: 1.65 !important;
        }
        .stMarkdown .period-label {
            margin: 0.95rem 0 0.45rem !important;
            text-align: center;
            color: #1b7fbf !important;
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            letter-spacing: 0.08em;
        }
        .stMarkdown .range-value {
            margin: 0.15rem 0 0.15rem !important;
            text-align: center;
            color: #16324a !important;
            font-size: 0.95rem !important;
            font-weight: 700 !important;
        }
        .stMarkdown .range-caption {
            margin: 0.7rem 0 0.3rem !important;
            text-align: center;
            color: #5c6b7a !important;
            font-size: 0.75rem !important;
            font-weight: 700 !important;
            letter-spacing: 0.08em;
        }
        [data-testid="stLayoutWrapper"]:has(.hero-card) [data-testid="stButtonGroup"] {
            width: 100%;
            margin: 0 auto 0.1rem;
        }
        [data-testid="stLayoutWrapper"]:has(.hero-card) button[data-variant="segmented_control"] {
            flex: 1 1 0;
            min-width: 0;
            padding-left: 0.1rem !important;
            padding-right: 0.1rem !important;
            font-size: 0.92rem !important;
        }
        [data-testid="stLayoutWrapper"]:has(.update-mark) [data-testid="stButton"] {
            width: 100% !important;
            margin-top: 0.85rem;
        }
        [data-testid="stLayoutWrapper"]:has(.update-mark) [data-testid="stButton"] > button {
            width: 100% !important;
        }
        .stMarkdown .wx-stats {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.85rem;
            height: 100%;
            align-content: center;
        }
        .stMarkdown .wx-stat {
            background: #f4f8fc;
            border-radius: 16px;
            padding: 1.05rem 1.1rem 0.95rem;
        }
        .stMarkdown .wx-stat p { margin: 0 !important; }
        .stMarkdown .wx-stat .k {
            color: #5c6b7a !important;
            font-size: 0.82rem !important;
        }
        .stMarkdown .wx-stat .v {
            margin-top: 0.15rem !important;
            color: #16324a !important;
            font-size: 1.65rem !important;
            font-weight: 720 !important;
            line-height: 1.15 !important;
        }
        .stMarkdown .wx-stat .lo { color: #2b6cb0 !important; }
        .stMarkdown .wx-stat .hi { color: #d64545 !important; }
        .stMarkdown .wx-range {
            display: flex;
            gap: 1.1rem;
            margin: 0.35rem 0 0.9rem !important;
            font-size: 1.15rem !important;
            font-weight: 700 !important;
        }
        .stMarkdown .wx-range .lo { color: #2b6cb0 !important; }
        .stMarkdown .wx-range .hi { color: #d64545 !important; }
        .stMarkdown .hour-row {
            display: grid;
            gap: 0.7rem;
            align-items: stretch;
        }
        .stMarkdown .hours {
            display: flex;
            gap: 0.45rem;
            overflow-x: auto;
            padding: 0.15rem 0 0.35rem;
        }
        .stMarkdown .hour {
            width: 100%;
            min-width: 0;
            box-sizing: border-box;
            text-align: center;
            background: #f7fbfe;
            border: 1px solid #e3ebf3;
            border-radius: 18px;
            padding: 0.75rem 0.3rem 0.65rem;
        }
        .stMarkdown .hour-dim { opacity: 0.45; }
        .stMarkdown .hour p { margin: 0 !important; }
        .stMarkdown .h-time { color: #5c6b7a !important; font-size: 0.82rem !important; }
        .stMarkdown .h-icon {
            display: flex !important;
            align-items: center;
            justify-content: center;
            height: 1.7rem;
            font-size: 1.35rem !important;
            line-height: 1 !important;
            margin: 0.35rem 0 0.1rem !important;
        }
        .stMarkdown .h-temp {
            color: #16324a !important;
            font-size: 1.35rem !important;
            font-weight: 700 !important;
            margin-top: 0.18rem !important;
        }
        .stMarkdown .h-cond { color: #5c6b7a !important; font-size: 0.75rem !important; margin-top: 0.15rem !important; }
        .stMarkdown .verdict {
            margin: 1.15rem 0 0.35rem;
            background: #ffffff;
            border: 1px solid #e3ebf3;
            border-radius: 18px;
            padding: 0.95rem 1.05rem 0.9rem;
        }
        .stMarkdown .verdict-kicker {
            margin: 0 !important;
            color: #1b7fbf !important;
            font-size: 0.84rem !important;
        }
        .stMarkdown .verdict-title {
            margin: 0.05rem 0 0.25rem !important;
            font-size: 3.4rem !important;
            font-weight: 720 !important;
            line-height: 1.05 !important;
            color: #1b6ca8 !important;
        }
        .stMarkdown .verdict-short { color: #d4533a !important; }
        .stMarkdown .verdict-detail {
            margin: 0 !important;
            color: #3d4d5c !important;
            font-size: 1.02rem !important;
            line-height: 1.6 !important;
        }
        [data-testid="stLayoutWrapper"]:has(.hero-pending) [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
            width: 100% !important;
            display: flex !important;
            justify-content: center;
            margin-top: 0.85rem;
        }
        [data-testid="stLayoutWrapper"]:has(.hero-pending) [data-testid="stButton"] > button {
            display: block !important;
            width: min(16rem, 72%) !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }
        [data-testid="stLayoutWrapper"]:has(.wx-layout) > [data-testid="stVerticalBlock"] {
            position: relative;
            padding: 1.45rem 1.4rem 1.55rem !important;
            gap: 0 !important;
        }
        [data-testid="stLayoutWrapper"]:has(.wx-layout) .wx-head {
            display: flex;
            align-items: center;
            box-sizing: border-box;
            height: 2rem;
            min-height: 2rem;
            margin: 0 0 1.35rem !important;
            padding-right: 7.2rem;
        }
        [data-testid="stLayoutWrapper"]:has(.wx-layout) .wx-head .card-label {
            margin: 0 !important;
            line-height: 2rem !important;
        }
        .stMarkdown .wx-layout {
            padding: 0;
        }
        .stMarkdown .wx-place { margin-bottom: 0.1rem !important; }
        .stMarkdown .wx-condition { margin-top: 0.2rem !important; }
        .stMarkdown .wx-temp { margin-top: 0.15rem !important; }
        .stMarkdown .wx-stat {
            padding: 1.05rem 1.1rem 0.95rem;
        }
        [data-testid="stLayoutWrapper"]:has(.wx-layout) [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
            position: absolute !important;
            top: 1.45rem;
            right: 1.4rem;
            left: auto !important;
            width: auto !important;
            height: 2rem !important;
            min-height: 0 !important;
            padding: 0 !important;
            z-index: 2;
            margin: 0 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: flex-end !important;
        }
        [data-testid="stLayoutWrapper"]:has(.wx-layout) [data-testid="stButton"] {
            display: flex !important;
            align-items: center !important;
            height: 2rem !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        [data-testid="stLayoutWrapper"]:has(.wx-layout) [data-testid="stButton"] button {
            min-height: 2rem !important;
            height: 2rem !important;
            margin: 0 !important;
            padding: 0 0.75rem !important;
            font-size: 0.78rem !important;
            line-height: 2rem !important;
            width: auto !important;
            border: 0 !important;
            border-radius: 10px !important;
            background: #1b7fbf !important;
            color: #ffffff !important;
            box-shadow: none !important;
        }
        [data-testid="stLayoutWrapper"]:has(.wx-layout) [data-testid="stButton"] button:disabled {
            background: #c5d0dc !important;
            color: #ffffff !important;
        }
        .pill {
            display: inline-block;
            padding: 0.18rem 0.62rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.02em;
            line-height: 1.4;
            color: #ffffff;
        }
        .pill-long { background: #1b3a5c; }
        .pill-short { background: #c65a3a; }
        .pill-cold { background: #2b6cb0; }
        .pill-ok { background: #2f855a; }
        .pill-hot { background: #c53030; }
        .pill-muted { background: #8aa0b3; }
        .pill-outer-none { background: #8aa0b3; }
        .pill-outer-light { background: #2f855a; }
        .pill-outer-sturdy { background: #1b4f8c; }
        .pill-outer-thick { background: #8a4b2f; }
        .log-table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
        .log-table th, .log-table td {
            padding: 0.55rem 0.4rem;
            text-align: left;
            border-bottom: 1px solid #e3ebf3;
            white-space: nowrap;
        }
        .log-table th { color: #5c6b7a; font-size: 0.78rem; font-weight: 700; }
        .log-scroll { overflow-x: auto; }
        .cal-month { margin: 0; font-weight: 700; color: #16324a; text-align: center; }
        .cal-nav { display: none; }
        [data-testid="stLayoutWrapper"]:has(.cal-nav) {
            background: #ffffff;
            border: 1px solid #e3ebf3 !important;
            border-radius: 24px;
            padding: 1rem 1.15rem 1.15rem;
            box-shadow: 0 10px 28px rgba(22, 50, 74, 0.06);
            margin-bottom: 0.9rem;
        }
        [data-testid="stLayoutWrapper"]:has(.cal-nav) [data-testid="stButton"] button {
            min-height: 2.2rem !important;
            background: #ffffff !important;
            color: #1b7fbf !important;
            border: 1px solid #d5e2ee !important;
            box-shadow: none !important;
        }
        [data-testid="stLayoutWrapper"]:has(.cal-nav) [data-testid="stButton"] button:hover {
            background: #e7f3fb !important;
            color: #1b7fbf !important;
        }
        [data-testid="stLayoutWrapper"]:has(.cal-nav) [data-testid="stButton"] button:disabled {
            background: #f4f8fc !important;
            color: #b7c6d4 !important;
            border-color: #e3ebf3 !important;
        }
        .cal-dow, .cal-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 0.35rem; }
        .cal-dow { margin-bottom: 0.35rem; color: #5c6b7a; font-size: 0.75rem; text-align: center; font-weight: 700; }
        .cal-blank { min-height: 4.2rem; }
        .cal-day {
            position: relative;
            min-height: 4.2rem;
            border-radius: 14px;
            border: 1px solid #e3ebf3;
            background: #f4f8fc;
            color: #8aa0b3;
            padding: 0.35rem 0.3rem 0.3rem;
            text-align: left;
            cursor: default;
        }
        .cal-day.is-long { background: #1b3a5c; border-color: #1b3a5c; color: #ffffff; cursor: pointer; }
        .cal-day.is-short { background: #c65a3a; border-color: #c65a3a; color: #ffffff; cursor: pointer; }
        .cal-day.is-open { background: #ffffff; color: #16324a; cursor: pointer; }
        .cal-num { display: block; font-size: 0.82rem; font-weight: 700; }
        .cal-tag { display: block; margin-top: 0.35rem; font-size: 0.72rem; font-weight: 700; }
        .cal-tip {
            display: none;
            position: absolute;
            z-index: 6;
            left: 50%;
            bottom: calc(100% + 6px);
            transform: translateX(-50%);
            background: #16324a;
            color: #ffffff;
            border-radius: 10px;
            padding: 0.4rem 0.55rem;
            font-size: 0.72rem;
            font-weight: 600;
            white-space: nowrap;
            pointer-events: none;
        }
        .cal-day:hover .cal-tip, .cal-day:focus .cal-tip { display: block; }
        .cal-legend { margin-top: 0.75rem; color: #5c6b7a; font-size: 0.8rem; }
        .cal-swatch {
            display: inline-block;
            width: 0.7rem;
            height: 0.7rem;
            border-radius: 3px;
            margin: 0 0.25rem 0 0.7rem;
            vertical-align: -1px;
        }
        .cal-swatch.long { background: #1b3a5c; margin-left: 0; }
        .cal-swatch.short { background: #c65a3a; }
        [data-testid="stLayoutWrapper"]:has(form) .section-title {
            margin-top: 0 !important;
        }
        .save-toast {
            position: fixed;
            top: 1rem;
            left: 50%;
            z-index: 80;
            width: min(28rem, calc(100% - 2rem));
            box-sizing: border-box;
            padding: 0.85rem 1.15rem 0.9rem 1rem;
            background: #ffffff;
            border: 1px solid #d7e6f2;
            border-left: 4px solid #1b7fbf;
            border-radius: 16px;
            box-shadow: 0 16px 40px rgba(22, 50, 74, 0.16);
            animation: save-toast 3.6s ease forwards;
            pointer-events: none;
        }
        .save-toast .save-kicker {
            margin: 0 !important;
            color: #1b7fbf !important;
            font-size: 0.74rem !important;
            font-weight: 700 !important;
            letter-spacing: 0.08em;
        }
        .save-toast .save-line {
            margin: 0.18rem 0 0 !important;
            color: #16324a !important;
            font-size: 1rem !important;
            font-weight: 700 !important;
            line-height: 1.45 !important;
        }
        @keyframes save-toast {
            0% { opacity: 0; transform: translate(-50%, -10px); }
            10% { opacity: 1; transform: translate(-50%, 0); }
            72% { opacity: 1; transform: translate(-50%, 0); }
            100% { opacity: 0; transform: translate(-50%, -8px); visibility: hidden; }
        }
        @media (max-width: 680px) {
            div[data-testid="stRadio"] div[role="radiogroup"] { grid-template-columns: 1fr; }
            .stMarkdown .hero-line { font-size: 1.7rem !important; }
            .stMarkdown .wx-temp { font-size: 3.6rem !important; }
            .stMarkdown .wx-layout { grid-template-columns: 1fr !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _current_page() -> str:
    page = st.session_state.get("nav")
    if page in ("📜 履歴", "履歴"):
        return "履歴"
    if page in ("Home", "境界線", "設定"):
        return page
    return "Home"


def _bottom_nav() -> str:
    labels = ["Home", "境界線", "履歴", "設定"]
    pages = ["Home", "境界線", "履歴", "設定"]
    current = _current_page()
    # 保存直後だけ、下のメニューの選択も Home に戻す。
    manual = 0 if st.session_state.pop("force_home", False) else None
    return option_menu(
        None,
        labels,
        icons=["house", "graph-up", "clock-history", "gear"],
        orientation="horizontal",
        default_index=pages.index(current),
        manual_select=manual,
        key="nav",
        styles={
            "container": {
                "padding": "0.35rem 0.45rem 0.45rem",
                "background-color": "#ffffff",
                "width": "100%",
                "margin": "0",
            },
            "icon": {"color": "var(--nav-icon)", "font-size": "1.15rem", "transition": "color 0.2s ease"},
            "nav-link": {
                "font-size": "0.78rem",
                "font-family": "Hiragino Sans, Hiragino Kaku Gothic ProN, Apple Color Emoji, Segoe UI Emoji, sans-serif",
                "text-align": "center",
                "--hover-color": "#e7f3fb",
                "--nav-icon": "#1b7fbf",
                "color": "#5c6b7a",
                "border-radius": "12px",
                "padding": "0.45rem 0.15rem",
                "transition": "background-color 0.2s ease, color 0.2s ease",
            },
            "nav-link-selected": {
                "background-color": "#1b7fbf",
                "color": "#ffffff",
                "--nav-icon": "#ffffff",
            },
        },
    )


def _settings_page(conn, profile) -> None:
    st.markdown("<p class='section-title'>アカウント</p>", unsafe_allow_html=True)
    st.caption("変えたあとは、今日の気象を取り直すと提案に反映されます。")
    _profile_form(conn, profile, "変更を保存", "settings")
    st.markdown("<div class='form-spacer'></div>", unsafe_allow_html=True)


def _topbar(today: str) -> None:
    shown = today.replace("-", ".")
    st.markdown(
        f"<div class='topbar'><p class='mark'>適正服装診断</p><p class='today'>{shown}</p></div>",
        unsafe_allow_html=True,
    )


def _section(title: str) -> None:
    st.markdown(f"<p class='section-title'>{title}</p>", unsafe_allow_html=True)


def _show_progress(slot, count: int) -> None:
    width = min(count, 10) * 10
    with slot.container():
        st.markdown(
            (
                "<div class='meter'>"
                "<div class='meter-row'>"
                "<p class='meter-label'>データ蓄積状況</p>"
                f"<p class='meter-value'>{count} / 10件</p>"
                "</div>"
                "<div class='meter-track'>"
                f"<div class='meter-fill' style='width:{width}%'></div>"
                "</div></div>"
            ),
            unsafe_allow_html=True,
        )


def _show_suggestion(slot, state, today_row, profile) -> None:
    constitution = None if profile is None else profile["constitution"]
    commute = None if profile is None else profile["commute"]
    outing = "day" if profile is None else profile["outing"]
    if today_row is None:
        clothing = None
        judged = None
    else:
        judged = judgment_temperature(today_row)
        wind = float(today_row["wind_speed"])
        if state.mode == "model" and state.model is not None:
            clothing = predict_clothing(state.model, judged, wind, float(today_row["humidity"]))
        else:
            clothing = suggest_clothing(judged, constitution, wind, commute)

    with slot.container():
        if today_row is None or clothing is None or judged is None:
            _proposal(
                "気象データを取得すると提案が表示されます",
                accumulation_note(0 if state.mode == "rules" else 10, state.mode),
                empty=True,
            )
            return

        label = CLOTHING_LABELS[clothing]
        temp_name = TEMP_NAMES.get(outing, "最高気温")
        wind = float(today_row["wind_speed"])
        if state.mode == "model":
            detail = (
                f"判定に使う{temp_name}は {judged:.1f}℃、"
                f"風速 {wind:.1f} m/s、"
                f"湿度 {float(today_row['humidity']):.0f}% です。"
            )
            kicker = "AI予測（ロジスティック回帰モデル）による提案"
        else:
            detail = _rule_detail(today_row, constitution, commute, outing, judged)
            kicker = (
                "両方の服装データが揃うとAIモデルが有効化されます"
                if state.mode == "single_class"
                else "現在は体質設定に基づいた初期ルールで提案中です"
            )
        _proposal(f"今日の提案：{label}", detail, kicker=kicker, clothing=clothing)


def _rule_detail(today_row, constitution: str | None, commute: str | None, outing: str, judged: float) -> str:
    threshold = threshold_c(constitution)
    body = CONSTITUTION_LABELS.get(constitution or "normal", "普通")
    temp_name = TEMP_NAMES.get(outing, "最高気温")
    wind = float(today_row["wind_speed"])
    effective = feels_like(judged, wind, commute)
    detail = (
        f"{body}の基準は {threshold:.0f}℃。"
        f"{threshold:.0f}℃未満は長袖、{threshold:.0f}℃以上は半袖です。"
    )
    if commute == "walk":
        detail += (
            f" 徒歩・自転車なので、風速 {wind:.1f} m/s を"
            f" {WIND_C_PER_MS['walk']}℃/m/s で体感に反映しています。"
        )
    elif commute == "transit":
        detail += (
            f" 電車・バスなので、風速 {wind:.1f} m/s を"
            f" {WIND_C_PER_MS['transit']}℃/m/s で少し反映しています。"
        )
    detail += f" 判定に使う{temp_name}は {judged:.1f}℃"
    if commute in ("walk", "transit"):
        detail += f"（体感 {effective:.1f}℃）"
    detail += "。"
    daily_max = float(today_row["temp_max"])
    if abs(judged - daily_max) >= 0.05:
        detail += f" 今日の最高気温は {daily_max:.1f}℃。"
    return detail


def _proposal(title: str, detail: str, *, kicker: str = "", clothing: str | None = None, empty: bool = False) -> None:
    classes = ["proposal"]
    if empty:
        classes.append("proposal-empty")
    elif clothing == "short":
        classes.append("proposal-short")
    title_class = "proposal-title proposal-title-empty" if empty else "proposal-title"
    kicker_html = f"<p class='proposal-kicker'>{kicker}</p>" if kicker else ""
    st.markdown(
        (
            f"<div class='{' '.join(classes)}'>"
            f"{kicker_html}"
            f"<p class='{title_class}'>{title}</p>"
            f"<p class='proposal-detail'>{detail}</p>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _show_chart(rows, model, today_row) -> None:
    labeled = [row for row in rows if row["clothing"] in CLOTHING_LABELS]
    if not labeled and today_row is None:
        st.caption("服装の記録が増えると、散布図が表示されます。")
        return

    st.plotly_chart(
        build_scatter(rows, model if labeled else None, today_row, "light"),
        width="stretch",
    )
    with st.expander("このグラフの見方・AIの仕組み"):
        st.markdown(
            "- **青い面**: 気温が低い、または風が強い側です。ここは長袖のゾーンです。\n"
            "- **橙の面**: 気温が高く、風が穏やかな側です。ここは半袖のゾーンです。\n"
            "- **黄色い線**: 過去データからAIが学習した、長袖と半袖の境目です。\n"
            "- **今日の位置**: 青い面なら長袖、橙の面なら半袖を提案しています。"
        )


def _onboarding(conn) -> None:
    st.markdown(
        "<h1 class='hero-title'>アカウントを作成</h1>"
        "<p class='lede'>体質と移動手段で、長袖か半袖かの基準をあなた用にします。あとから変えられます。</p>",
        unsafe_allow_html=True,
    )
    _profile_form(conn, get_profile(conn), "アカウントを作成", "onboarding")


def _profile_form(conn, profile, submit_label: str, form_key: str) -> None:
    constitution = None if profile is None else profile["constitution"]
    commute = None if profile is None else profile["commute"]
    stored_outing = None if profile is None else profile["outing"]
    outing = stored_outing if stored_outing in {code for code, _ in OUTING_OPTIONS} else "day"
    with st.form(form_key):
        _question("① 体質（ベース体感）")
        body = st.radio(
            "体質",
            [label for _, label in CONSTITUTION_OPTIONS],
            index=_option_index(CONSTITUTION_OPTIONS, constitution, 1),
            label_visibility="collapsed",
        )
        _question("② 主な移動手段")
        ride = st.radio(
            "移動手段",
            [label for _, label in COMMUTE_OPTIONS],
            index=_option_index(COMMUTE_OPTIONS, commute, 0),
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button(submit_label, type="primary")
    if submitted:
        save_profile(
            conn,
            _code_for(CONSTITUTION_OPTIONS, body),
            _code_for(COMMUTE_OPTIONS, ride),
            outing,
        )
        if form_key == "settings":
            st.session_state["nav"] = "Home"
            st.session_state["force_home"] = True
        st.rerun()


def _question(title: str) -> None:
    st.markdown(
        f"<div class='q-block'><p class='q-title'>{html.escape(title)}</p></div>",
        unsafe_allow_html=True,
    )


def _option_index(options, code: str | None, default: int) -> int:
    codes = [item[0] for item in options]
    return codes.index(code) if code in codes else default


def _code_for(options, label: str) -> str:
    for code, name in options:
        if name == label:
            return code
    return options[0][0]


def _weather_key(coordinates, today: str, outing: str):
    if coordinates is None:
        return None
    return (round(coordinates[0], 3), round(coordinates[1], 3), today, outing, "morning")


def _request_location_refresh() -> None:
    st.session_state.show_clothing = None
    st.session_state.show_feedback = None
    st.session_state.refresh_location = True
    st.session_state.pop("ip_coordinates", None)
    st.session_state.pop("auto_weather", None)


def _prepare_location() -> bool:
    if _query_value("loc") == "fresh":
        st.session_state.refresh_location = False
        st.session_state.pop("ip_coordinates", None)
        st.session_state.pop("auto_weather", None)
        st.query_params.pop("loc", None)
        return False
    return bool(st.session_state.get("refresh_location"))


def _refresh_weather(conn, config, today: str, profile) -> None:
    coordinates, _origin = _current_coordinates()
    outing = "day" if profile is None else profile["outing"]
    auto_key = _weather_key(coordinates, today, outing)
    should_fetch = config.api_key and auto_key is not None and st.session_state.get("auto_weather") != auto_key
    if not should_fetch:
        return
    before = st.session_state.get("auto_weather")
    _fetch_and_save(conn, config, coordinates, profile, auto_key)
    if st.session_state.get("auto_weather") != before:
        st.rerun()


def _render_today(conn, config, today: str, row, state, profile, count: int) -> None:
    if not config.api_key:
        st.warning(".env に OPENWEATHER_API_KEY を設定すると、気象を取得できます。")
    if row is None:
        st.info("今日の気象はまだありません。")
        if st.button("現在地の気象を取得", disabled=not config.api_key):
            _request_location_refresh()
            st.rerun()
        return

    forecast = _load_forecast(row)
    commute = None if profile is None else profile["commute"]
    constitution = None if profile is None else profile["constitution"]
    shown = forecast["now_temp"]
    low, high = _day_range(forecast["hours"], row)
    revealed = st.session_state.get("show_clothing") == today
    shown_bounds = _applied_bounds(forecast["hours"]) if revealed else _range_bounds(forecast["hours"])
    window = _window_between(forecast["hours"], shown_bounds)
    slot = decisive_hour(window, commute) if window else None
    if slot is None:
        judged, wind, humidity = judgment_temperature(row), float(row["wind_speed"]), float(row["humidity"])
    else:
        judged, wind, humidity = float(slot["temp"]), float(slot["wind"]), float(slot["humidity"])

    if state.mode == "model" and state.model is not None:
        clothing = predict_clothing(state.model, judged, wind, humidity)
    else:
        clothing = suggest_clothing(judged, constitution, wind, commute)
    daylight = None
    if slot is not None:
        clothing, daylight = sunlight_clothing(
            clothing,
            int(slot["hour"]),
            float(slot["temp"]),
            str(slot.get("condition") or ""),
        )
    if shown is None:
        shown = judged

    active = {int(hour["hour"]) for hour in window}
    outer = suggest_period_outer(float(slot["temp"])) if slot is not None else _outer_advice(forecast["hours"], row)
    if revealed:
        _suggestion_card(clothing, slot, state.mode, outer, forecast["hours"], window, daylight)
    elif _predict_card(forecast["hours"]):
        draft = _range_bounds(forecast["hours"])
        if draft is not None:
            st.session_state["applied_range"] = draft
        st.session_state.show_clothing = today
        st.rerun()
    _current_weather_card(
        conn,
        config,
        profile,
        today,
        city=str(row["city_name"]),
        condition=forecast["condition"],
        temp=float(shown),
        low=low,
        high=high,
        wind=float(row["wind_speed"]),
        humidity=float(row["humidity"]),
    )
    _hourly_card(forecast["hours"], active)


def _fetch_and_save(conn, config, coordinates, profile, auto_key) -> None:
    outing = "day" if profile is None else profile["outing"]
    commute = None if profile is None else profile["commute"]
    try:
        if coordinates is None:
            weather = fetch_today(config.api_key, config.city, config.country, period=outing)
        else:
            weather = fetch_today_at(config.api_key, coordinates[0], coordinates[1], period=outing)
    except WeatherError as exc:
        st.error(str(exc))
        return

    hours = list(weather.hours)
    previous = get_log(conn, weather.log_date)
    if previous is not None:
        hours = list(merge_hours(_load_forecast(previous).get("hours") or [], hours))
    window = outing_hours(hours, outing)
    slot = decisive_hour(window, commute) if window else None
    save_weather(
        conn,
        log_date=weather.log_date,
        temp_max=weather.temp_max,
        wind_speed=float(slot["wind"]) if slot else weather.wind_speed,
        humidity=float(slot["humidity"]) if slot else weather.humidity,
        city_name=weather.city_name,
        decision_temp=float(slot["temp"]) if slot else weather.decision_temp,
        forecast_json=json.dumps(
            {"now_temp": weather.now_temp, "condition": weather.condition, "hours": hours},
            ensure_ascii=False,
        ),
    )
    if auto_key is not None:
        st.session_state.auto_weather = auto_key


def _load_forecast(row) -> dict:
    raw = row["forecast_json"] if "forecast_json" in row.keys() else None
    if not raw:
        return {"now_temp": None, "condition": "", "hours": []}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {"now_temp": None, "condition": "", "hours": []}
    return {
        "now_temp": data.get("now_temp"),
        "condition": str(data.get("condition") or ""),
        "hours": list(data.get("hours") or []),
    }


def _day_range(hours, row) -> tuple[float, float]:
    if not hours:
        temp = float(row["temp_max"])
        return temp, temp
    low = min(float(hour["temp"]) for hour in hours)
    high = max(float(hour.get("temp_max", hour["temp"])) for hour in hours)
    return low, high


def _day_detail(all_hours, window, slot, constitution: str | None, commute: str | None) -> str:
    low, high = _day_range(window or all_hours, {"temp_max": slot["temp"]})
    feels = feels_like(float(slot["temp"]), float(slot["wind"]), commute)
    threshold = threshold_c(constitution)
    body = CONSTITUTION_LABELS.get(constitution or "normal", "普通")
    relation = "下回るので長袖" if feels < threshold else "上回るので半袖"
    wind_note = ""
    if commute == "walk":
        wind_note = f" 徒歩・自転車なので風を {WIND_C_PER_MS['walk']}℃/m/s で反映しています。"
    elif commute == "transit":
        wind_note = f" 電車・バスなので風を {WIND_C_PER_MS['transit']}℃/m/s で反映しています。"
    return (
        f"今日の予報は {low:.0f}〜{high:.0f}℃。"
        f"いちばん冷える {int(slot['hour'])}時は {float(slot['temp']):.0f}℃、体感 {feels:.0f}℃で、"
        f"{body}の基準 {threshold:.0f}℃を{relation}です。{wind_note}"
    )


def _hour_label(hour) -> str:
    return f"{int(hour['hour'])}時"


def _forecast_hours(hours) -> list:
    return sorted(hours or [], key=lambda hour: int(hour["hour"]))


def _default_hour_label(hours) -> str:
    moment = datetime.now().hour + datetime.now().minute / 60
    best = min(hours, key=lambda hour: (abs(int(hour["hour"]) - moment), int(hour["hour"])))
    return _hour_label(best)


def _range_bounds(hours) -> tuple[str, str] | None:
    items = _forecast_hours(hours)
    if not items:
        return None
    labels = [_hour_label(hour) for hour in items]
    default = _default_hour_label(items)
    start = st.session_state.get("predict_start")
    end = st.session_state.get("predict_end")
    if start not in labels:
        start = default
    if end not in labels:
        end = default
    if labels.index(start) > labels.index(end):
        previous = st.session_state.get("predict_span")
        moved_start = isinstance(previous, (list, tuple)) and len(previous) == 2 and start != previous[0]
        if moved_start:
            end = start
        else:
            start = end
    return start, end


def _applied_bounds(hours) -> tuple[str, str] | None:
    items = _forecast_hours(hours)
    if not items:
        return None
    labels = [_hour_label(hour) for hour in items]
    chosen = st.session_state.get("applied_range")
    if (
        isinstance(chosen, (list, tuple))
        and len(chosen) == 2
        and chosen[0] in labels
        and chosen[1] in labels
    ):
        start, end = labels.index(chosen[0]), labels.index(chosen[1])
        if start > end:
            start, end = end, start
        return labels[start], labels[end]
    return _range_bounds(hours)


def _window_between(hours, bounds) -> list:
    items = _forecast_hours(hours)
    if not items or bounds is None:
        return []
    labels = [_hour_label(hour) for hour in items]
    start, end = labels.index(bounds[0]), labels.index(bounds[1])
    return items[start : end + 1]


def _remember_range(start: str, end: str) -> None:
    if st.session_state.get("predict_start") != start:
        st.session_state["predict_start"] = start
    if st.session_state.get("predict_end") != end:
        st.session_state["predict_end"] = end
    st.session_state["predict_span"] = (start, end)


def _hour_picker(hours) -> None:
    items = _forecast_hours(hours)
    bounds = _range_bounds(hours)
    if not items or bounds is None:
        return
    start, end = bounds
    _remember_range(start, end)
    labels = [_hour_label(hour) for hour in items]
    shown = start if start == end else f"{start}〜{end}"
    st.markdown(
        f"<p class='period-label'>出かける時間</p><p class='range-value'>{html.escape(shown)}</p>",
        unsafe_allow_html=True,
    )
    st.markdown("<p class='range-caption'>始まり</p>", unsafe_allow_html=True)
    st.segmented_control(
        "始まり",
        labels,
        key="predict_start",
        label_visibility="collapsed",
        width="stretch",
        required=True,
    )
    st.markdown("<p class='range-caption'>終わり</p>", unsafe_allow_html=True)
    st.segmented_control(
        "終わり",
        labels,
        key="predict_end",
        label_visibility="collapsed",
        width="stretch",
        required=True,
    )


def _range_update(hours) -> None:
    draft = _range_bounds(hours)
    applied = _applied_bounds(hours)
    if draft is None or applied is None or draft == applied:
        return
    start, end = draft
    label = start if start == end else f"{start}〜{end}"
    st.markdown("<div class='update-mark'></div>", unsafe_allow_html=True)
    if st.button(f"{label}で更新", key="apply_range", type="primary", width="stretch"):
        st.session_state["applied_range"] = draft
        st.rerun()


def _predict_card(hours) -> bool:
    with st.container(border=True):
        st.markdown(
            "<div class='hero-card hero-pending'>"
            "<p class='hero-line'>今日の服装を予想</p>"
            "<p class='hero-note'>始まりと終わりを選ぶと、そのあいだのいちばん寒い時間で服装を出します。</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        _hour_picker(hours)
        return st.button("服装を予想", type="primary")


_OUTER_WITH = {
    "thick": "厚手の上着",
    "sturdy": "しっかりめの上着",
    "light": "薄手の上着",
}
_OUTER_BECAUSE = {
    "thick": "厚手のアウターが必要です",
    "sturdy": "ジャケットやパーカーが必要です",
    "light": "脱ぎ着しやすい薄手の上着がおすすめです",
    "none": "1日の気温差が小さいため、上着なしで大丈夫です",
}


def _conclusion(clothing: str, outer) -> str:
    label = CLOTHING_LABELS[clothing]
    extra = _OUTER_WITH.get(outer.level)
    if extra is None:
        return label
    return f"{label} ＋ {extra}"


def _one_reason(slot, outer, window, daylight: str | None = None) -> str:
    advice = _OUTER_BECAUSE[outer.level]
    if slot is None or not window:
        return advice
    hour = int(slot["hour"])
    temp = float(slot["temp"])
    start = int(window[0]["hour"])
    end = int(window[-1]["hour"])
    place = f"{hour}時" if start == end else f"{start}時〜{end}時でいちばん冷える{hour}時"
    light = {"night": "日差しがないため", "sun": "晴れで日差しがあるため"}.get(daylight or "")
    if light == "日差しがないため":
        body = f"{place}は{temp:.0f}℃で、{light}、長袖です"
    elif light:
        body = f"{place}は{temp:.0f}℃でも、{light}、半袖です"
    elif outer.level == "none":
        return f"{place}は{temp:.0f}℃で、上着なしで大丈夫です"
    else:
        return f"{place}は{temp:.0f}℃のため、{advice}"
    if outer.level == "none":
        return f"{body}。上着なしで大丈夫です"
    return f"{body}。{advice}"


def _outer_advice(hours, row):
    low, high = _day_range(hours, row)
    night = next((float(hour["temp"]) for hour in hours if int(hour["hour"]) == 21), None)
    return suggest_outer(high, low, night)


def _suggestion_card(clothing: str, slot, mode: str, outer, hours, window, daylight: str | None) -> None:
    tone = "hero-short" if clothing == "short" else "hero-long"
    status = "🤖 AI判定" if mode == "model" else "🌱 初期ルール"
    with st.container(border=True):
        st.markdown(
            f"<div class='hero-card {tone}'>"
            f"<p class='hero-status'>{status}</p>"
            f"<p class='hero-line'>{html.escape(_conclusion(clothing, outer))}</p>"
            f"<p class='hero-detail'>{html.escape(_one_reason(slot, outer, window, daylight))}</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        _hour_picker(hours)
        _range_update(hours)


def _weather_icon(condition: str) -> str:
    text = condition or ""
    if "雷" in text:
        return "⛈️"
    if "雪" in text:
        return "❄️"
    if "霧" in text and "雨" not in text:
        return "🌫️"
    if any(word in text for word in ("小雨", "霧雨", "にわか", "弱")):
        return "🌦️"
    if "雨" in text:
        return "🌧️"
    if any(word in text for word in ("曇", "雲", "くもり")):
        return "🌤️" if ("薄" in text or "少" in text) else "☁️"
    if "晴" in text:
        return "☀️"
    return ""


def _weather_icon_html(condition: str, css: str) -> str:
    icon = _weather_icon(condition)
    if not icon:
        return ""
    return f"<span class='{css}'>{icon}</span>"


def _current_weather_card(
    conn,
    config,
    profile,
    today: str,
    *,
    city: str,
    condition: str,
    temp: float,
    low: float,
    high: float,
    wind: float,
    humidity: float,
) -> None:
    with st.container(border=True):
        if st.button(
            "天気を更新",
            type="primary",
            key="refresh",
            disabled=not config.api_key,
            help="現在地を取り直して、天気を更新します。",
        ):
            _request_location_refresh()
            st.rerun()
        st.markdown(
            "<div class='wx-head'><p class='card-label'>現在の天気</p></div>"
            "<div class='wx-layout'>"
            "<div>"
            f"<p class='wx-place'>{html.escape(city)}</p>"
            f"<p class='wx-condition'>{_weather_icon_html(condition, 'wx-icon')}<span class='wx-word'>{html.escape(condition)}</span></p>"
            f"<p class='wx-temp'>{temp:.0f}°</p>"
            "</div>"
            "<div class='wx-stats'>"
            f"<div class='wx-stat'><p class='k'>最低</p><p class='v lo'>{low:.0f}°</p></div>"
            f"<div class='wx-stat'><p class='k'>最高</p><p class='v hi'>{high:.0f}°</p></div>"
            f"<div class='wx-stat'><p class='k'>風速</p><p class='v'>{wind:.1f} m/s</p></div>"
            f"<div class='wx-stat'><p class='k'>湿度</p><p class='v'>{humidity:.0f}%</p></div>"
            "</div></div>",
            unsafe_allow_html=True,
        )


def _hourly_card(hours, active: set[int]) -> None:
    if not hours:
        st.markdown(
            "<div class='panel'><p class='card-label'>時間別予報</p><p class='hero-note'>時間別の予報はまだありません。</p></div>",
            unsafe_allow_html=True,
        )
        return
    cells = []
    for hour in hours:
        dim = " hour-dim" if int(hour["hour"]) not in active else ""
        condition = str(hour.get("condition") or "")
        cells.append(
            f"<div class='hour{dim}'>"
            f"<p class='h-time'>{int(hour['hour'])}時</p>"
            f"{_weather_icon_html(condition, 'h-icon')}"
            f"<p class='h-temp'>{float(hour['temp']):.0f}°</p>"
            f"<p class='h-cond'>{html.escape(condition)}</p>"
            "</div>"
        )
    count = len(hours)
    st.markdown(
        "<div class='panel'>"
        "<p class='card-label'>時間別予報</p>"
        f"<div class='hour-row' style='grid-template-columns: repeat({count}, minmax(0, 1fr))'>"
        f"{''.join(cells)}</div></div>",
        unsafe_allow_html=True,
    )


def _current_coordinates() -> tuple[tuple[float, float] | None, str]:
    browser = _browser_coordinates()
    if browser is not None:
        return browser, "browser"
    if "ip_coordinates" not in st.session_state:
        st.session_state.ip_coordinates = locate_by_ip()
    ip_coordinates = st.session_state.ip_coordinates
    if ip_coordinates is not None:
        return ip_coordinates, "ip"
    return None, "city"


def _browser_coordinates() -> tuple[float, float] | None:
    try:
        lat = float(_query_value("lat"))
        lon = float(_query_value("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _query_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    if value is None:
        return None
    return str(value)


def _ask_browser_location(force: bool = False) -> None:
    if not force and (_browser_coordinates() is not None or _query_value("geo") == "denied"):
        return
    maximum_age = 0 if force else 300000
    fresh = "true" if force else "false"
    st.html(
        f"""
        <div id="sleeve-loc" hidden></div>
        <script>
        (() => {{
          const params = new URLSearchParams(window.location.search);
          const force = {fresh};
          const needsFix = force || (!params.has("lat") && params.get("geo") !== "denied");
          if (!needsFix || window.__sleeveLocRequest) return;
          window.__sleeveLocRequest = true;
          const finish = (lat, lon, denied) => {{
            window.__sleeveLocRequest = false;
            const url = new URL(window.location.href);
            if (denied) {{
              url.searchParams.delete("lat");
              url.searchParams.delete("lon");
              url.searchParams.set("geo", "denied");
            }} else {{
              url.searchParams.set("lat", String(lat));
              url.searchParams.set("lon", String(lon));
              url.searchParams.delete("geo");
            }}
            if (force) url.searchParams.set("loc", "fresh");
            window.location.replace(url.toString());
          }};
          if (!navigator.geolocation) {{
            finish(null, null, true);
            return;
          }}
          navigator.geolocation.getCurrentPosition(
            (pos) => finish(pos.coords.latitude, pos.coords.longitude, false),
            () => finish(null, null, true),
            {{enableHighAccuracy: false, timeout: 8000, maximumAge: {maximum_age}}}
          );
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def _review_open(today: str) -> bool:
    return (
        st.session_state.get("show_clothing") == today
        and st.session_state.get("show_feedback") == today
    )


def _reflect_button(today: str) -> None:
    st.markdown("<div class='reflect-mark'></div>", unsafe_allow_html=True)
    if st.button("服装を振り返る", key="reflect", width="stretch"):
        st.session_state.show_feedback = today
        st.rerun()


def _show_saved_note(note) -> None:
    if isinstance(note, str):
        line = html.escape(note)
    else:
        clothing, outer, sensation = note
        line = " · ".join(html.escape(part) for part in (clothing, outer, sensation))
    st.html(
        "<div class='save-toast'>"
        "<p class='save-kicker'>保存しました</p>"
        f"<p class='save-line'>{line}</p>"
        "</div>",
        unsafe_allow_javascript=False,
    )


def _feedback_screen(conn, today: str) -> None:
    st.markdown("<div class='back-mark'></div>", unsafe_allow_html=True)
    if st.button("ホームに戻る", key="back_home"):
        st.session_state.show_feedback = None
        st.rerun()
    _feedback_section(conn, today)


def _feedback_section(conn, today: str) -> None:
    with st.container(border=True):
        _section("今日の服装と体感")
        row = get_log(conn, today)
        if row is None:
            st.info("気象を保存してから、着たものと体感を記録できます。")
        else:
            with st.form("feedback"):
                st.markdown("<div class='feedback-mark'></div>", unsafe_allow_html=True)
                clothing = _choice("実際の服装", CLOTHING_LABELS_OPTIONS, _saved_code(row, "clothing"), 0)
                outer = _choice("上着・羽織りもの", OUTER_OPTIONS, _saved_number(row, "outer"), 0)
                sensation = _choice("体感", SENSATION_OPTIONS, _saved_sensation(row), 0)
                period = _choice(
                    "特に寒かった/暑かった時間帯",
                    FEEL_PERIOD_OPTIONS,
                    _saved_code(row, "feel_period"),
                    "all",
                )
                submitted = st.form_submit_button("記録を保存", type="primary")

            if submitted:
                sensation_value = _code_for(SENSATION_OPTIONS, sensation)
                save_feedback(
                    conn,
                    log_date=today,
                    clothing=_code_for(CLOTHING_LABELS_OPTIONS, clothing),
                    sensation=SENSATION_CODES[sensation_value],
                    outer=_code_for(OUTER_OPTIONS, outer),
                    feel_period=_code_for(FEEL_PERIOD_OPTIONS, period),
                )
                st.session_state.show_feedback = None
                st.session_state.saved_note = (clothing, outer, sensation)
                st.rerun()
    st.markdown("<div class='form-spacer'></div>", unsafe_allow_html=True)


CLOTHING_LABELS_OPTIONS = tuple(CLOTHING_LABELS.items())


def _choice(title: str, options, selected, default):
    st.markdown(f"<p class='field-label'>{html.escape(title)}</p>", unsafe_allow_html=True)
    labels = [label for _, label in options]
    return st.radio(
        title,
        labels,
        index=_option_index(options, selected, _option_index(options, default, 0)),
        label_visibility="collapsed",
    )


def _saved_code(row, name: str):
    value = row[name]
    return None if value is None else value


def _saved_number(row, name: str):
    value = row[name]
    return None if value is None else int(value)


def _saved_sensation(row):
    value = row["sensation_value"]
    if value in SENSATION_CODES:
        return int(value)
    return {code: score for score, code in SENSATION_CODES.items()}.get(row["sensation"])


def _history_section(conn) -> None:
    rows = list_logs(conn)
    if not rows:
        st.info("記録はまだありません。")
        return

    st.caption(f"{len(rows)} 件")
    view = st.segmented_control(
        "履歴の表示",
        ["カレンダー", "リスト"],
        default="カレンダー",
        key="history_view",
        label_visibility="collapsed",
        width="stretch",
    )
    if view == "リスト":
        _history_list(rows)
    else:
        _history_calendar(rows)
    st.markdown("<div class='form-spacer'></div>", unsafe_allow_html=True)


def _month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _month_from_index(index: int) -> tuple[int, int]:
    return index // 12, index % 12 + 1


def _history_month(rows) -> tuple[int, int, int, int]:
    today = date.today()
    first = min(date.fromisoformat(row["log_date"]) for row in rows)
    earliest = _month_index(first.year, first.month)
    latest = _month_index(today.year, today.month)
    chosen = st.session_state.get("history_month")
    if isinstance(chosen, int) and earliest <= chosen <= latest:
        shown = chosen
    else:
        shown = latest
    st.session_state["history_month"] = shown
    year, month = _month_from_index(shown)
    return year, month, earliest, latest


def _history_calendar(rows) -> None:
    year, month, earliest, latest = _history_month(rows)
    by_date = {row["log_date"]: row for row in rows}
    weeks = calendar.Calendar(firstweekday=calendar.SUNDAY).monthdayscalendar(year, month)
    cells = []
    for week in weeks:
        for day in week:
            if day == 0:
                cells.append("<div class='cal-blank'></div>")
                continue
            key = date(year, month, day).isoformat()
            cells.append(_calendar_day(day, by_date.get(key)))
    heads = "".join(f"<div>{name}</div>" for name in ("日", "月", "火", "水", "木", "金", "土"))
    with st.container(border=True):
        st.markdown("<div class='cal-nav'></div>", unsafe_allow_html=True)
        previous, title, following = st.columns([1, 2.2, 1], vertical_alignment="center")
        with previous:
            if st.button("前月", key="cal_prev", disabled=_month_index(year, month) <= earliest, width="stretch"):
                st.session_state["history_month"] = _month_index(year, month) - 1
                st.rerun()
        with title:
            st.markdown(f"<p class='cal-month'>{year}年{month}月</p>", unsafe_allow_html=True)
        with following:
            if st.button("次月", key="cal_next", disabled=_month_index(year, month) >= latest, width="stretch"):
                st.session_state["history_month"] = _month_index(year, month) + 1
                st.rerun()
        st.markdown(
            f"<div class='cal-dow'>{heads}</div>"
            f"<div class='cal-grid'>{''.join(cells)}</div>"
            "<p class='cal-legend'><span class='cal-swatch long'></span>長袖"
            "<span class='cal-swatch short'></span>半袖</p>",
            unsafe_allow_html=True,
        )


def _calendar_day(day: int, row) -> str:
    if row is None:
        return f"<div class='cal-day'><span class='cal-num'>{day}</span></div>"
    clothing = row["clothing"]
    if clothing == "long":
        tone, label = "is-long", "長袖"
    elif clothing == "short":
        tone, label = "is-short", "半袖"
    else:
        tone, label = "is-open", "未記録"
    sensation = SENSATION_LABELS.get(row["sensation"], "未記録")
    tip = f"最高 {float(row['temp_max']):.1f}℃ · 風速 {float(row['wind_speed']):.1f} m/s · {sensation}"
    return (
        f"<div class='cal-day {tone}' tabindex='0'>"
        f"<span class='cal-num'>{day}</span>"
        f"<span class='cal-tag'>{label}</span>"
        f"<span class='cal-tip'>{html.escape(tip)}</span>"
        "</div>"
    )


def _history_list(rows) -> None:
    body = []
    for row in rows:
        clothing = row["clothing"]
        if clothing == "long":
            cloth = "<span class='pill pill-long'>長袖</span>"
        elif clothing == "short":
            cloth = "<span class='pill pill-short'>半袖</span>"
        else:
            cloth = "<span class='pill pill-muted'>未記録</span>"
        sensation = row["sensation"]
        if sensation in SENSATION_TONES:
            feel = f"<span class='pill {SENSATION_TONES[sensation]}'>{SENSATION_LABELS[sensation]}</span>"
        else:
            feel = "<span class='pill pill-muted'>未記録</span>"
        outer = OUTER_TABLE.get(None if row["outer"] is None else int(row["outer"]), "未記録")
        body.append(
            "<tr>"
            f"<td>{html.escape(row['log_date'])}</td>"
            f"<td>{float(row['temp_max']):.1f}</td>"
            f"<td>{judgment_temperature(row):.1f}</td>"
            f"<td>{float(row['wind_speed']):.1f}</td>"
            f"<td>{float(row['humidity']):.0f}</td>"
            f"<td>{cloth}</td>"
            f"<td>{html.escape(outer)}</td>"
            f"<td>{feel}</td>"
            "</tr>"
        )
    st.markdown(
        "<div class='panel log-scroll'><table class='log-table'><thead><tr>"
        "<th>日付</th><th>最高気温</th><th>判定気温</th><th>風速</th><th>湿度</th>"
        "<th>服装</th><th>羽織り</th><th>体感</th>"
        "</tr></thead><tbody>"
        f"{''.join(body)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
