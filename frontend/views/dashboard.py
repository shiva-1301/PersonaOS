"""Your numbers: streak, completions over time, goal progress, memory health."""

import streamlit as st

from frontend import charts, session, style


def _dark() -> bool:
    return st.context.theme.type == "dark"


def render() -> None:
    api = session.client()
    data = api.analytics()
    style.hero(
        "Last 30 days",
        "Your progress, *at a glance*.",
        f"Days and weeks are in your timezone ({data['timezone']}).",
        left=True,
        live=True,
    )
    dark = _dark()

    streak, week, overdue, soon = st.columns(4)
    s = data["streak"]
    streak.metric(
        "Current streak",
        f"{s['current']} day{'s' if s['current'] != 1 else ''}",
        help=f"Days in a row with at least one completed task. Longest this year: {s['longest']}.",
    )
    week.metric("Completed, last 7 days", data["completed_last_7_days"])
    overdue.metric("Overdue", data["tasks"]["overdue"])
    soon.metric(f"Due in the next {data['due_soon_hours']} h", data["tasks"]["due_soon"])
    if s["current"] and not s["completed_today"]:
        st.caption("Finish a task today to keep your streak going.")

    st.subheader("Tasks completed")
    weekly = (
        st.segmented_control(
            "Shown per",
            ["Day", "Week"],
            default="Day",
            required=True,
            label_visibility="collapsed",
        )
        == "Week"
    )
    points = data["completions_per_week"] if weekly else data["completions_per_day"]
    if any(p["completed"] for p in points):
        st.plotly_chart(
            charts.completions_figure(points, weekly=weekly, dark=dark),
            width="stretch",
            config=charts.PLOTLY_CONFIG,
        )
    else:
        st.info("No completed tasks in this period yet. Tick off a task and it shows up here.")
    with st.expander("Show data"):
        st.dataframe(points, hide_index=True, width="stretch")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Goal progress")
        if data["goals"]:
            st.plotly_chart(
                charts.goals_figure(data["goals"], dark=dark),
                width="stretch",
                config=charts.PLOTLY_CONFIG,
            )
            with st.expander("Show data"):
                st.dataframe(
                    [
                        {
                            "goal": g["title"],
                            "status": g["status"],
                            "done": g["done"],
                            "total": g["total"],
                        }
                        for g in data["goals"]
                    ],
                    hide_index=True,
                    width="stretch",
                )
        else:
            st.info("No goals yet. Add one on the Goals & Tasks page.")
    with right:
        st.subheader("Memory")
        if sum(data["memory_by_state"].values()):
            st.plotly_chart(
                charts.memory_figure(data["memory_by_state"], dark=dark),
                width="stretch",
                config=charts.PLOTLY_CONFIG,
            )
            with st.expander("Show data"):
                st.dataframe(
                    [{"state": k, "memories": v} for k, v in data["memory_by_state"].items()],
                    hide_index=True,
                    width="stretch",
                )
        else:
            st.info("No memories yet. Tell PersonaOS about yourself in Chat.")
