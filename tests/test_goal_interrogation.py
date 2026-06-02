"""Goal planner static Q&A validation."""

from todai.goal_planner.interrogation import (
    answers_complete,
    parse_answer,
    parse_confirmation,
)
from todai.goal_planner.router import route_goal_turn


def test_parse_difficulty_synonyms():
    assert parse_answer("difficulty", "pretty hard").parsed == "hard"
    assert parse_answer("difficulty", "easy").parsed == "easy"
    assert not parse_answer("difficulty", "banana").valid


def test_parse_tasks_per_day():
    assert parse_answer("tasks_per_day", "2").parsed == 2
    assert not parse_answer("tasks_per_day", "ten").valid
    assert not parse_answer("tasks_per_day", "9").valid


def test_parse_minutes():
    assert parse_answer("minutes_per_day", "90").parsed == 90
    assert parse_answer("minutes_per_day", "1 hour").parsed == 60
    assert not parse_answer("minutes_per_day", "morning").valid


def test_parse_minutes_range():
    r = parse_answer("minutes_per_day", "10 to 15 mints")
    assert r.valid
    assert r.parsed == 12
    assert "10" in r.display and "15" in r.display


def test_parse_minutes_range_not_summed():
    """Should not treat 10 and 15 as separate values that add to 25."""
    r = parse_answer("minutes_per_day", "10 to 15 minutes")
    assert r.parsed == 12
    assert r.parsed != 25


def test_parse_objective_ok_default():
    r = parse_answer("objective", "ok", default_objective="Learn Python basics")
    assert r.valid and r.parsed == "Learn Python basics"


def test_answers_complete():
    answers = {
        "objective": {"valid": True, "parsed": "Learn Python"},
        "difficulty": {"valid": True, "parsed": "medium"},
        "tasks_per_day": {"valid": True, "parsed": 2},
        "minutes_per_day": {"valid": True, "parsed": 60},
    }
    assert answers_complete(answers)


def test_router_interrogate_when_incomplete():
    out = route_goal_turn(message="hello", phase="interrogate", answers={})
    assert out.route == "goal_interrogate"


def test_router_confirm_phase():
    answers = {
        "objective": {"valid": True, "parsed": "x"},
        "difficulty": {"valid": True, "parsed": "easy"},
        "tasks_per_day": {"valid": True, "parsed": 1},
        "minutes_per_day": {"valid": True, "parsed": 30},
    }
    out = route_goal_turn(message="yes", phase="confirm", answers=answers)
    assert out.route == "goal_confirm"


def test_confirmation_yes():
    assert parse_confirmation("yes") == "yes"
    assert parse_confirmation("go ahead") == "yes"
