import time

from tf import ai
from tf.rules import Student, validate_plan

PL, WP, TT = "기획자", "웹 프로그래머", "테스터"
TEAMS = {1: [Student("1", "학생1", PL)], 2: [Student("2", "학생2", WP)]}
UNASSIGNED = [Student("3", "학생3", WP), Student("4", "학생4", PL, (TT,))]


def run(monkeypatch, fake):
    monkeypatch.setattr(ai, "call_model", fake)
    attempts = []
    plan, source = ai.assign(TEAMS, UNASSIGNED, "key", lambda *a: None,
                             lambda n, m, outcome, d, e: attempts.append(outcome))
    return plan, source, attempts


def test_slow_models_are_cut_and_app_solves(monkeypatch):
    monkeypatch.setattr(ai, "TOTAL_TIMEOUT", 1)

    def slow(*args):
        time.sleep(3)
        return "{}"

    start = time.monotonic()
    plan, source, attempts = run(monkeypatch, slow)
    assert time.monotonic() - start < 6
    assert attempts == ["시간 초과"] * 3
    assert "앱이" in source
    assert validate_plan(TEAMS, UNASSIGNED, plan) == []


def test_bad_plan_moves_to_next_model(monkeypatch):
    answers = iter([
        "",                                                                      # 빈 응답
        '{"assignments": [{"student_no": "3", "team": 2}, {"student_no": "4", "team": 1}]}',  # 주 역할 중복
        '```json\n{"assignments": [{"student_no": "3", "team": "팀1"}, {"student_no": "4", "team": 2}]}\n```',
    ])

    def fake(*args):
        text = next(answers)
        if not text:
            raise ai.AIFailure("빈 응답")
        return text

    plan, source, attempts = run(monkeypatch, fake)
    assert attempts == ["빈 응답", "검사 실패", "성공"]
    assert source.startswith("3차")
    assert sorted(plan) == [("3", 1), ("4", 2)]
