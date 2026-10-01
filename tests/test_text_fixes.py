"""Weekdays written next to explicit dates are corrected; nothing else changes."""

from datetime import date

import pytest

from app.agent.text_fixes import fix_weekdays, flag_false_claims

TODAY = date(2026, 10, 1)  # a Thursday


@pytest.mark.parametrize(
    ("written", "fixed"),
    [
        # Seen in the Docker replays of the English-exam conversation:
        ("**Monday, 2026-10-02, 14:00-16:00**", "**Friday, 2026-10-02, 14:00-16:00**"),
        ("- Mon 2026-10-02 14:00-16:00", "- Fri 2026-10-02 14:00-16:00"),
        ("Monday 2026-10-09: 15:00-17:00", "Friday 2026-10-09: 15:00-17:00"),
        ("Monday, October 2, 2026, 14:00", "Friday, October 2, 2026, 14:00"),
        ("Tuesday, October 3", "Saturday, October 3"),
        ("Wednesday 4th October", "Sunday 4th October"),
        ("MONDAY (2026-10-02)", "FRIDAY (2026-10-02)"),
        ("on monday, oct 2", "on friday, oct 2"),
    ],
)
def test_wrong_weekdays_next_to_dates_are_corrected(written, fixed):
    assert fix_weekdays(written, TODAY) == fixed


@pytest.mark.parametrize(
    "text",
    [
        "Friday, 2026-10-02 is fine",
        "Fri 2026-10-02 and Sun 2026-10-04",
        "Study on Monday and Wednesday afternoons.",  # no date: nothing to check
        "Monday: 14:00-16:00",
        "Your exam is on 2026-10-12.",
        "Monday, February 30",  # not a real date: left alone
        "Mondays are hard. October 2 is a Friday.",
    ],
)
def test_correct_or_unrelated_text_is_unchanged(text):
    assert fix_weekdays(text, TODAY) == text


def test_a_date_without_a_year_means_the_nearest_one():
    # In late December, "January 4" is next year: 2027-01-04 is a Monday.
    assert fix_weekdays("Friday, January 4", date(2026, 12, 20)) == "Monday, January 4"


FAILED_PLAN = [{"tool": "generate_study_plan", "ok": False}]


@pytest.mark.parametrize(
    "reply",
    [
        # Seen in the Docker replays after a failed generate_study_plan call:
        "I've created a goal for your English exam on 2026-10-12. Let's plan for 5 hours.",
        "I've created a goal for your English exam and a study plan with 5 hours per week.",
        "Your study plan has been created!",
    ],
)
def test_claims_after_only_failed_tools_get_a_correction(reply):
    fixed = flag_false_claims(reply, FAILED_PLAN)
    assert fixed.startswith(reply)
    assert fixed.endswith(
        "(Correction: nothing was created or changed yet. I couldn't make the study plan.)"
    )


@pytest.mark.parametrize(
    ("reply", "results"),
    [
        ("I've created your study plan.", [{"tool": "generate_study_plan", "ok": True}]),
        # One tool worked: the claim may be about it.
        (
            "I've added the task.",
            [{"tool": "generate_study_plan", "ok": False}, {"tool": "add_task", "ok": True}],
        ),
        ("I'll create the goal once you tell me the date.", FAILED_PLAN),  # a plan, not a claim
        ("Let's create the goal first. When is the exam?", FAILED_PLAN),
        ("I've created a goal for you.", []),  # no tools at all: nothing to compare against
    ],
)
def test_honest_replies_are_unchanged(reply, results):
    assert flag_false_claims(reply, results) == reply
