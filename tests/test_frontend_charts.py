"""frontend/charts.py: the figures carry the right data and follow the palette rules."""

from datetime import date, timedelta

from frontend.charts import DARK, LIGHT, completions_figure, goals_figure, memory_figure


def days(n: int) -> list[dict]:
    start = date(2026, 9, 2)
    return [{"date": (start + timedelta(days=i)).isoformat(), "completed": i % 3} for i in range(n)]


def test_completions_one_series_one_hue_per_mode():
    light = completions_figure(days(30))
    dark = completions_figure(days(30), dark=True)
    (bar,) = light.data
    assert len(bar.x) == 30 and list(bar.y) == [i % 3 for i in range(30)]
    assert bar.marker.color == LIGHT.series[0] and dark.data[0].marker.color == DARK.series[0]
    assert light.layout.showlegend is False  # a single series: the title names it
    assert light.layout.barcornerradius == 4


def test_weekly_completions_use_week_starts():
    weeks = [
        {"week_start": "2026-09-21", "completed": 2},
        {"week_start": "2026-09-28", "completed": 5},
    ]
    (bar,) = completions_figure(weeks, weekly=True).data
    assert [str(x) for x in bar.x] == ["2026-09-21", "2026-09-28"]
    assert "Week of" in bar.hovertemplate


def test_goal_meters_show_done_share_over_a_full_track():
    goals = [
        {"title": "Finish ML course", "done": 1, "total": 3, "ratio": 1 / 3},
        {"title": "Learn Spanish", "done": 0, "total": 0, "ratio": 0.0},
    ]
    track, fill = goals_figure(goals).data
    assert list(track.x) == [100, 100] and track.marker.color == LIGHT.track
    assert list(fill.x) == [33.3, 0.0] and fill.marker.color == LIGHT.series[0]
    assert fill.text[0] == "33.3% · 1/3"


def test_memory_donut_colour_follows_the_state_not_its_rank():
    full = memory_figure({"active": 4, "stale": 2, "archived": 1, "superseded": 1}).data[0]
    no_active = memory_figure({"active": 0, "stale": 2, "archived": 0, "superseded": 1}).data[0]
    colours = dict(zip(full.labels, full.marker.colors, strict=True))
    assert colours == {
        "Active": LIGHT.series[0],
        "Stale": LIGHT.series[1],
        "Archived": LIGHT.series[2],
        "Superseded": LIGHT.series[3],
    }
    # Empty states are left out, and the survivors keep their colours.
    assert list(no_active.labels) == ["Stale", "Superseded"]
    assert list(no_active.marker.colors) == [LIGHT.series[1], LIGHT.series[3]]
    fig = memory_figure({"active": 4, "stale": 2, "archived": 1, "superseded": 1})
    assert fig.layout.showlegend is True and "label" in fig.data[0].textinfo
    assert "8" in fig.layout.annotations[0].text
