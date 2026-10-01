"""Goals with progress, study-plan generation, and tasks (add, filter, complete, delete)."""

from datetime import date, datetime, time, timedelta

import streamlit as st

from frontend import session, style
from frontend.api_client import ApiClient

FILTERS = {"All open": None, "Today": "today", "This week": "this_week", "Overdue": "overdue"}


def _due(iso: str | None, tz) -> str:
    return session.local_time(iso, tz) if iso else "no due date"


def _new_goal(api: ApiClient) -> None:
    with st.expander("New goal"), st.form("new-goal", clear_on_submit=True):
        title = st.text_input("Goal", placeholder="e.g. Finish the ML course")
        has_date = st.checkbox("It has a target date", value=True)
        target = st.date_input("Target date", value=date.today() + timedelta(days=60))
        description = st.text_area("Notes (optional)")
        if st.form_submit_button("Create goal", type="primary"):
            if not title.strip():
                st.error("Give the goal a name.")
            elif session.attempt(
                api.create_goal,
                title.strip(),
                target_date=target if has_date else None,
                description=description.strip() or None,
            ):
                session.flash(f'Goal "{title.strip()}" created.')
                st.rerun()


def _plan_form(api: ApiClient, goal: dict) -> None:
    with st.form(f"plan-{goal['id']}"):
        st.markdown("**Generate a study plan**")
        hours = st.number_input(
            "Hours per week", min_value=0.5, max_value=80.0, value=6.0, step=0.5
        )
        prefs = st.text_input(
            "Preferences (optional)", placeholder="e.g. weekday evenings, no Sundays"
        )
        if not goal["target_date"]:
            st.caption("This goal has no target date: the plan needs an end date.")
            end = st.date_input("Plan until", value=date.today() + timedelta(days=30))
        else:
            end = None
        if st.form_submit_button("Generate plan", type="primary"):
            with st.spinner("Planning with the local model. This can take a couple of minutes…"):
                plan = session.attempt(
                    api.generate_plan,
                    goal["id"],
                    hours,
                    end_date=end,
                    preferences=prefs.strip() or None,
                )
            if plan:
                note = f'{len(plan["tasks"])} study sessions added to "{goal["title"]}".'
                if plan["adjustments"]:
                    note += " " + " ".join(plan["adjustments"])
                session.flash(note)
                st.rerun()


def _goal(api: ApiClient, goal: dict, tz) -> None:
    p = goal["progress"]
    with st.container(key=f"card-goal-{goal['id']}"):
        head, status = st.columns([4, 1], vertical_alignment="center")
        head.markdown(f"**{goal['title']}**")
        target = (
            f"target {date.fromisoformat(goal['target_date']):%d %b %Y}"
            if goal["target_date"]
            else "no target date"
        )
        head.caption(
            f"{goal['status'].capitalize()} · {target} · {p['done']} of {p['total']} tasks done"
        )
        new_status = status.selectbox(
            "Status",
            ["active", "paused", "completed"],
            index=["active", "paused", "completed"].index(goal["status"]),
            key=f"goal-status-{goal['id']}",
            label_visibility="collapsed",
        )
        if new_status != goal["status"] and session.attempt(
            api.update_goal, goal["id"], status=new_status
        ):
            st.rerun()
        style.meter(p["ratio"], f"{round(100 * p['ratio'])}% done")
        with st.expander("Plan and tasks"):
            _plan_form(api, goal)
            for task in api.tasks(goal_id=goal["id"]):
                _task(api, task, prefix="g", tz=tz)
            with st.popover("Delete goal"):
                st.write("Delete this goal? Its tasks are kept, without the goal link.")
                sure = st.button("Yes, delete it", key=f"del-goal-{goal['id']}", type="primary")
                if sure and session.attempt(api.delete_goal, goal["id"]):
                    session.flash(f'Goal "{goal["title"]}" deleted.')
                    st.rerun()


def _task(api: ApiClient, task: dict, prefix: str, tz) -> None:
    done = task["status"] == "done"
    box, delete = st.columns([12, 1], vertical_alignment="center")
    label = f"~~{task['title']}~~" if done else task["title"]
    extra = f" · {task['est_minutes']} min" if task["est_minutes"] else ""
    checked = box.checkbox(
        f"{label}  \n:gray[{_due(task['due_at'], tz)}{extra}]",
        value=done,
        key=f"{prefix}-task-{task['id']}",
    )
    if checked != done and session.attempt(
        api.update_task, task["id"], status="done" if checked else "todo"
    ):
        st.rerun()
    remove = delete.button("✕", key=f"{prefix}-del-{task['id']}", help="Delete task")
    if remove and session.attempt(api.delete_task, task["id"]):
        st.rerun()


def _new_task(api: ApiClient, goals: list[dict]) -> None:
    with st.expander("New task"), st.form("new-task", clear_on_submit=True):
        title = st.text_input("Task")
        has_due = st.checkbox("It has a due date")
        day, at = st.columns(2)
        due_day = day.date_input("Due", value=date.today())
        due_time = at.time_input("Time", value=time(18, 0))
        goal_ids = [None] + [g["id"] for g in goals]
        names = {g["id"]: g["title"] for g in goals}
        goal_id = st.selectbox(
            "Goal (optional)", goal_ids, format_func=lambda g: names.get(g, "No goal")
        )
        minutes = st.number_input(
            "Minutes (optional)", min_value=0, max_value=1440, value=0, step=15
        )
        if st.form_submit_button("Add task", type="primary"):
            if not title.strip():
                st.error("Give the task a name.")
            elif session.attempt(
                api.create_task,
                title.strip(),
                # Naive local time: the API reads it in your profile's timezone.
                due_at=datetime.combine(due_day, due_time) if has_due else None,
                goal_id=goal_id,
                est_minutes=minutes or None,
            ):
                session.flash(f'Task "{title.strip()}" added.')
                st.rerun()


def render() -> None:
    api = session.client()
    tz = session.user_zone(api)
    style.hero(
        "Goals & tasks",
        "Plans that *happen*.",
        "Set a goal, let PersonaOS plan the study sessions, then tick them off.",
        left=True,
    )
    goals_tab, tasks_tab = st.tabs(["Goals", "Tasks"])

    goals = api.goals()
    with goals_tab:
        _new_goal(api)
        if not goals:
            st.info('No goals yet. Create one above, or ask in Chat: "Add a goal to …".')
        for goal in goals:
            _goal(api, goal, tz)

    with tasks_tab:
        _new_task(api, goals)
        choice = st.segmented_control(
            "Show", list(FILTERS) + ["Done"], default="All open", required=True
        )
        if choice == "Done":
            tasks = api.tasks(status="done")
        else:
            tasks = [t for t in api.tasks(due=FILTERS.get(choice)) if t["status"] != "done"]
        if not tasks:
            st.info("Nothing here.")
        for task in tasks:
            _task(api, task, prefix="t", tz=tz)
