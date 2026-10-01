"""Plotly figures for the dashboard (pure functions: data in, figure out).

Colours come from the reference data-viz palette, with separate light and dark steps.
Single-series charts use one hue and no legend. The memory donut uses the first four
categorical slots in a fixed order (an entity always keeps its colour), with direct
labels plus a legend. Text uses ink tokens, never series colours, and grids are
hairline and recessive. Each chart also has a table view on the page.
"""

from dataclasses import dataclass
from datetime import date

import plotly.graph_objects as go


@dataclass(frozen=True)
class Theme:
    ink: str
    ink_secondary: str
    muted: str
    grid: str
    baseline: str
    surface: str
    series: tuple[str, str, str, str]  # categorical slots 1-4: blue, orange, aqua, yellow
    track: str  # meter track: same ramp as the fill, near the surface


LIGHT = Theme(
    ink="#1c1b22",  # the UI's ink (frontend/style.py)
    ink_secondary="#55525f",
    muted="#898781",
    grid="#e1e0d9",
    baseline="#c3c2b7",
    surface="#fcfcfb",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100"),
    track="#cde2fb",
)
DARK = Theme(
    ink="#ffffff",
    ink_secondary="#c3c2b7",
    muted="#898781",
    grid="#2c2c2a",
    baseline="#383835",
    surface="#1a1a19",
    series=("#3987e5", "#d95926", "#199e70", "#c98500"),
    track="#104281",
)
FONT = 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif'
MEMORY_STATES = ("active", "stale", "archived", "superseded")


def theme_for(dark: bool) -> Theme:
    return DARK if dark else LIGHT


def _layout(fig: go.Figure, t: Theme, *, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": FONT, "color": t.ink_secondary, "size": 13},
        hoverlabel={"font": {"family": FONT}},
        showlegend=False,
        bargap=0.25,
        barcornerradius=4,
    )
    fig.update_xaxes(showgrid=False, linecolor=t.baseline, tickfont={"color": t.muted})
    fig.update_yaxes(
        gridcolor=t.grid,
        gridwidth=1,
        zeroline=False,
        tickfont={"color": t.muted},
        rangemode="tozero",
    )
    return fig


def completions_figure(
    points: list[dict], *, weekly: bool = False, dark: bool = False
) -> go.Figure:
    """Tasks completed per day (or per week): one series, one hue, a bar per period."""
    t = theme_for(dark)
    key = "week_start" if weekly else "date"
    xs = [date.fromisoformat(str(p[key])) for p in points]
    ys = [p["completed"] for p in points]
    label = "Week of %{x|%d %b}" if weekly else "%{x|%a %d %b}"
    fig = go.Figure(
        go.Bar(
            x=xs,
            y=ys,
            marker={"color": t.series[0]},
            hovertemplate=f"{label}<br>%{{y}} completed<extra></extra>",
        )
    )
    _layout(fig, t, height=260)
    fig.update_yaxes(tickformat=",d", dtick=max(1, -(-max(ys or [0]) // 4)))
    fig.update_xaxes(tickformat="%d %b")
    return fig


def goals_figure(goals: list[dict], *, dark: bool = False) -> go.Figure:
    """Goal progress as meters: a same-ramp track (100%) with the done share on top."""
    t = theme_for(dark)
    titles = [g["title"] if len(g["title"]) <= 40 else g["title"][:39] + "…" for g in goals]
    pct = [round(100 * g["ratio"], 1) for g in goals]
    text = [f"{p:g}% · {g['done']}/{g['total']}" for p, g in zip(pct, goals, strict=True)]
    fig = go.Figure(
        [
            go.Bar(
                y=titles,
                x=[100] * len(goals),
                orientation="h",
                marker={"color": t.track},
                hoverinfo="skip",
            ),
            go.Bar(
                y=titles,
                x=pct,
                orientation="h",
                marker={"color": t.series[0]},
                text=text,
                textposition="outside",
                textfont={"color": t.ink_secondary},
                cliponaxis=False,
                customdata=[[g["done"], g["total"]] for g in goals],
                hovertemplate="%{y}<br>%{x:g}% done (%{customdata[0]} of %{customdata[1]})"
                "<extra></extra>",
            ),
        ]
    )
    _layout(fig, t, height=max(120, 46 * len(goals) + 40))
    fig.update_layout(
        barmode="overlay",
        bargap=0.62,
        barcornerradius="50%",
        margin={"l": 8, "r": 110, "t": 8, "b": 8},
    )
    fig.update_xaxes(range=[0, 100], showticklabels=False, showline=False)
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont={"color": t.ink})
    return fig


def memory_figure(by_state: dict[str, int], *, dark: bool = False) -> go.Figure:
    """Part-to-whole of memory states (4 fixed slices), labelled directly."""
    t = theme_for(dark)
    states = [s for s in MEMORY_STATES if by_state.get(s, 0) > 0]
    colors = [t.series[MEMORY_STATES.index(s)] for s in states]
    total = sum(by_state.get(s, 0) for s in MEMORY_STATES)
    fig = go.Figure(
        go.Pie(
            labels=[s.capitalize() for s in states],
            values=[by_state[s] for s in states],
            hole=0.6,
            sort=False,
            direction="clockwise",
            marker={"colors": colors, "line": {"color": t.surface, "width": 2}},
            textinfo="label+value",
            textposition="outside",
            textfont={"color": t.ink_secondary},
            hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
        )
    )
    _layout(fig, t, height=300)
    fig.update_layout(
        showlegend=True,
        legend={"orientation": "h", "y": -0.08, "font": {"color": t.ink_secondary}},
        margin={"l": 24, "r": 24, "t": 24, "b": 24},
        annotations=[
            {
                "text": f"<b>{total}</b><br>memories",
                "showarrow": False,
                "font": {"size": 15, "color": t.ink},
            }
        ],
    )
    return fig
