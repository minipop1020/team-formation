"""관리자 화면의 AI 배정 진행 안내를 확인한다. DB 상태와 AI 호출은 가짜로 바꿔 실제 데이터와 무료 호출 횟수를 쓰지 않는다."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tf import ai, service
from tf.db import get_secret

pytestmark = pytest.mark.skipif(not (get_secret("DATABASE_URL") or "").startswith("postgres"),
                                reason="DATABASE_URL 없음")

APP = str(Path(__file__).parent.parent / "streamlit_app.py")
MODEL = "nvidia/nemotron-3.5-super-vl:free"


def student(sid, no, main):
    return {"id": sid, "student_no": no, "name": f"학생{sid}", "main_role": main, "subs": [], "is_admin": False}


FAKE_STATE = {
    "students": {1: student(1, "9001", "기획자"), 2: student(2, "9002", "웹 프로그래머"),
                 3: student(3, "9003", "웹 프로그래머"), 4: student(4, "9004", "기획자")},
    "teams": [{"id": 1, "no": 1, "name": "팀1", "member_ids": [1]},
              {"id": 2, "no": 2, "name": "팀2", "member_ids": [2]}],
    "member_team": {1: 1, 2: 2},
    "pending": [],
    "latest_app": {},
    "applications": [],
    "closed": True,
    "confirmed": False,
}


def admin_app(monkeypatch, assign):
    monkeypatch.setattr(service, "load_state", lambda conn: FAKE_STATE)
    monkeypatch.setattr(service, "log_ai", lambda *args: None)
    monkeypatch.setattr(service, "recent_ai_logs", lambda limit=20: [])
    monkeypatch.setattr(ai, "assign", assign)
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["user"] = {"id": 0, "no": "admin", "name": "관리자", "is_admin": True}
    at.run()
    return at


def fake_assign(teams, unassigned, api_key, on_wait, on_attempt):
    on_wait(1, MODEL, 1.0)
    on_attempt(1, MODEL, "성공", "", 7.0)
    return [("9003", 1), ("9004", 2)], f"1차 모델({MODEL})의 배정안"


def test_assign_status_shows_each_attempt_once(monkeypatch):
    at = admin_app(monkeypatch, fake_assign)
    at.button(key="admin_assign").click().run()

    texts = [m.value for m in at.markdown]
    attempt_lines = [t for t in texts if "1차 모델" in t and "성공" in t]
    assert len(attempt_lines) == 1, attempt_lines
    # 모델 이름이 코드 모양이어야 ':free:' 가 🆓 로 바뀌지 않음
    assert f"`{MODEL}`" in attempt_lines[0]
    assert at.session_state["plan"] == [("9003", 1), ("9004", 2)]


def test_ai_switch_off_assigns_without_calling_ai(monkeypatch):
    def must_not_call(*args):
        raise AssertionError("AI를 끄면 AI를 부르면 안 됨")

    at = admin_app(monkeypatch, must_not_call)
    assert at.toggle(key="admin_use_ai").value is True      # 기본값은 AI 사용(채점 때 AI를 부름)
    at.toggle(key="admin_use_ai").set_value(False).run()
    at.button(key="admin_assign").click().run()
    plan = at.session_state["plan"]
    assert sorted(no for no, _ in plan) == ["9003", "9004"]
    assert "AI를 끄고" in at.session_state["plan_source"]


def test_student_sees_history_and_later_status(monkeypatch):
    state = dict(FAKE_STATE, closed=False, member_team={1: 1, 2: 2, 3: 1},
                 teams=[{"id": 1, "no": 1, "name": "팀1", "member_ids": [1, 3]},
                        {"id": 2, "no": 2, "name": "팀2", "member_ids": [2]}],
                 latest_app={3: {"student_id": 3, "team_no": 1, "status": "승인", "reason": "팀원 모두 찬성"}},
                 applications=[{"id": 7, "student_id": 3, "team_no": 1, "status": "승인",
                                "reason": "팀원 모두 찬성", "at": "05:00:00"}])
    monkeypatch.setattr(service, "load_state", lambda conn: state)
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["user"] = {"id": 3, "no": "9003", "name": "학생3", "is_admin": False}
    at.session_state["last_action"] = "apply"
    at.session_state["result"] = "대기: 팀1 팀원 1명의 투표를 기다림"
    at.run()
    captions = " ".join(c.value for c in at.caption)
    assert "그 뒤 바뀐 상태" in captions and "승인" in captions
    assert len(at.dataframe) == 1
    # 결과 안내(result) 자체는 단추를 누른 때의 결과 그대로
    assert any("대기: 팀1" in e.value for e in at.info)
