"""最高気温×風速の散布図と、ロジスティック回帰の決定境界。"""

from __future__ import annotations

from typing import Mapping, Sequence

import plotly.graph_objects as go

from sleeve.model import SleeveModel, labeled_rows
from sleeve.suggest import judgment_temperature

LONG_COLOR = "#1b3a5c"
SHORT_COLOR = "#c65a3a"
BOUNDARY_COLOR = "#F2C14E"
TODAY_COLOR = "#16324a"
LONG_FILL = "rgba(27, 58, 92, 0.22)"
SHORT_FILL = "rgba(198, 90, 58, 0.20)"


def build_scatter(
    rows: Sequence[Mapping],
    model: SleeveModel | None,
    today: Mapping | None = None,
    theme: str = "dark",
) -> go.Figure:
    dark = theme != "light"
    text = "#F4F6F8" if dark else "#1F2933"
    grid = "rgba(255,255,255,0.12)" if dark else "rgba(15,23,42,0.12)"
    outline = "#111418" if dark else "#FFFFFF"
    labeled = labeled_rows(rows)
    x_range, y_range = _view_range(labeled, today, model)
    fig = go.Figure()
    if model is not None and x_range is not None and y_range is not None:
        _add_zone_fill(fig, model, x_range, y_range)
        line_temps, line_winds = _boundary_on_box(model, x_range, y_range)
        if len(line_temps) == 2:
            fig.add_trace(
                go.Scatter(
                    x=line_temps,
                    y=line_winds,
                    mode="lines",
                    name="判定ライン",
                    line={"color": BOUNDARY_COLOR, "width": 3},
                )
            )
    _add_class(fig, labeled, "long", "長袖", LONG_COLOR, outline)
    _add_class(fig, labeled, "short", "半袖", SHORT_COLOR, outline)

    if today is not None:
        today_x = judgment_temperature(today)
        today_y = float(today["wind_speed"])
        fig.add_trace(
            go.Scatter(
                x=[today_x],
                y=[today_y],
                mode="markers",
                showlegend=False,
                hoverinfo="skip",
                marker={"color": "#ffffff", "size": 30, "symbol": "diamond", "line": {"width": 0}},
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[today_x],
                y=[today_y],
                mode="markers",
                name="今日",
                marker={
                    "color": TODAY_COLOR,
                    "size": 18,
                    "symbol": "diamond",
                    "line": {"color": BOUNDARY_COLOR, "width": 3},
                },
            )
        )

    annotations = []
    if today is not None:
        annotations.append(
            {
                "x": today_x,
                "y": today_y,
                "text": "今日",
                "showarrow": False,
                "xanchor": "center",
                "yanchor": "bottom",
                "yshift": 32,
                "font": {"size": 13, "color": text},
            }
        )

    fig.update_layout(
        template="plotly_dark" if dark else "plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"size": 15, "color": text},
        margin={"l": 68, "r": 28, "t": 12, "b": 56},
        height=480,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0, "font": {"size": 14}},
        annotations=annotations,
    )
    fig.update_xaxes(
        title_text="判定気温 (℃)",
        gridcolor=grid,
        zeroline=False,
        title_font_size=15,
        range=x_range,
    )
    fig.update_yaxes(
        title_text="風速 (m/s)",
        gridcolor=grid,
        zeroline=False,
        title_font_size=15,
        title_standoff=10,
        range=y_range,
    )
    return fig


def _view_range(rows: Sequence[Mapping], today: Mapping | None, model: SleeveModel | None):
    xs = [judgment_temperature(row) for row in rows]
    ys = [float(row["wind_speed"]) for row in rows]
    if today is not None:
        xs.append(judgment_temperature(today))
        ys.append(float(today["wind_speed"]))
    if not xs or not ys:
        return None, None
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    span_x = max(xmax - xmin, 2.0)
    span_y = max(ymax - ymin, 1.0)
    return [xmin - span_x * 0.08, xmax + span_x * 0.08], [max(0.0, ymin - span_y * 0.16), ymax + span_y * 0.18]


def _score(model: SleeveModel, temp: float, wind: float) -> float:
    temp_coef, wind_coef, humidity_coef = model.coefficients
    return temp_coef * temp + wind_coef * wind + model.intercept + humidity_coef * model.humidity_reference


def _add_zone_fill(fig: go.Figure, model: SleeveModel, x_range: list[float], y_range: list[float]) -> None:
    """湿度を平均に固定し、境目の直線でグラフを二つの面に分ける。"""
    xmin, xmax = x_range
    ymin, ymax = y_range
    corners = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
    long_side = _clip_side(model, corners, positive=True)
    short_side = _clip_side(model, corners, positive=False)
    _add_region(fig, long_side, LONG_FILL)
    _add_region(fig, short_side, SHORT_FILL)


def _clip_side(model: SleeveModel, corners: list[tuple[float, float]], positive: bool) -> list[tuple[float, float]]:
    kept: list[tuple[float, float]] = []
    for index, current in enumerate(corners):
        previous = corners[index - 1]
        prev_long = _score(model, *previous) >= 0
        curr_long = _score(model, *current) >= 0
        prev_in = prev_long if positive else not prev_long
        curr_in = curr_long if positive else not curr_long
        if curr_in != prev_in:
            kept.append(_cross(model, previous, current))
        if curr_in:
            kept.append(current)
    return kept


def _cross(model: SleeveModel, start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    start_score = _score(model, *start)
    end_score = _score(model, *end)
    share = start_score / (start_score - end_score)
    return (
        start[0] + (end[0] - start[0]) * share,
        start[1] + (end[1] - start[1]) * share,
    )


def _boundary_on_box(model: SleeveModel, x_range: list[float], y_range: list[float]) -> tuple[list[float], list[float]]:
    xmin, xmax = x_range
    ymin, ymax = y_range
    corners = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
    hits: list[tuple[float, float]] = []
    for index, current in enumerate(corners):
        previous = corners[index - 1]
        if (_score(model, *previous) >= 0) == (_score(model, *current) >= 0):
            continue
        point = _cross(model, previous, current)
        if not any(abs(point[0] - kept[0]) < 1e-6 and abs(point[1] - kept[1]) < 1e-6 for kept in hits):
            hits.append(point)
    if len(hits) < 2:
        return [], []
    hits.sort()
    return [hits[0][0], hits[-1][0]], [hits[0][1], hits[-1][1]]


def _add_region(fig: go.Figure, points: list[tuple[float, float]], color: str) -> None:
    if len(points) < 3:
        return
    closed = [*points, points[0]]
    fig.add_trace(
        go.Scatter(
            x=[point[0] for point in closed],
            y=[point[1] for point in closed],
            mode="lines",
            fill="toself",
            fillcolor=color,
            line={"width": 0},
            hoverinfo="skip",
            showlegend=False,
        )
    )


def _add_class(fig: go.Figure, rows: Sequence[Mapping], clothing: str, name: str, color: str, outline: str) -> None:
    chosen = [row for row in rows if row["clothing"] == clothing]
    if not chosen:
        return
    fig.add_trace(
        go.Scatter(
            x=[judgment_temperature(row) for row in chosen],
            y=[float(row["wind_speed"]) for row in chosen],
            mode="markers",
            name=name,
            marker={"color": color, "size": 13, "line": {"color": outline, "width": 1.5}},
        )
    )
