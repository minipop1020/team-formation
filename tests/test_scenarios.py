"""부록의 연습용 시나리오 3개를 실제 DB로 돌려, 결과 안내의 첫 단어가 기대 결과와 같은지 확인한다.

DB 상태를 초기화하므로 채점 기간에는 돌리지 않는다. DATABASE_URL 이 없으면 건너뛴다.
"""
from pathlib import Path

import pytest

from tf import service
from tf.db import get_secret, transaction
from tf.rules import solve, validate_plan

pytestmark = pytest.mark.skipif(not (get_secret("DATABASE_URL") or "").startswith("postgres"),
                                reason="DATABASE_URL 없음")

CSV_DIR = Path(__file__).parent / "scenarios"


class Runner:
    def __init__(self, csv_name, team_count, prefix):
        msg = service.reset_roster((CSV_DIR / csv_name).read_bytes(), team_count)
        assert msg.startswith("승인"), msg
        self.prefix = prefix
        self.ids = {s["student_no"]: i for i, s in self.state()["students"].items()}

    def state(self):
        with transaction() as conn:
            return service.load_state(conn)

    def sid(self, n):
        return self.ids[f"{self.prefix}{n:02d}"]

    def apply(self, n, team):
        return service.apply(self.sid(n), team)

    def vote(self, voter, applicant, yes=True):
        app = next(p for p in self.state()["pending"] if p["student_id"] == self.sid(applicant))
        return service.vote(self.sid(voter), app["id"], yes)

    def assign_and_confirm(self):
        state = self.state()
        teams, unassigned = service.teams_as_students(state), service.unassigned_students(state)
        plan = solve(teams, unassigned)
        assert plan is not None
        assert validate_plan(teams, unassigned, plan) == []
        msg = service.confirm_plan(plan)
        assert msg.startswith("승인"), msg
        final = self.state()
        assert not service.unassigned_students(final)
        from tf.rules import team_errors
        for no, members in service.teams_as_students(final).items():
            assert team_errors(no, members) == []


def word(msg):
    return msg.split(":")[0]


def test_easy():
    r = Runner("쉬움.csv", 3, "20261")
    assert word(r.apply(1, 1)) == "승인"
    assert word(r.apply(2, 2)) == "승인"
    assert word(r.apply(3, 3)) == "승인"
    assert word(r.apply(4, 1)) == "대기"
    assert word(r.vote(1, 4)) == "승인"
    assert word(r.apply(5, 2)) == "대기"
    assert word(r.vote(2, 5)) == "승인"
    assert word(r.apply(7, 1)) == "대기"
    assert word(r.vote(1, 7)) == "대기"
    assert word(r.vote(4, 7, False)) == "거절"
    assert word(r.apply(6, 3)) == "대기"
    assert word(r.vote(3, 6)) == "승인"
    assert word(service.close_recruiting()) == "승인"
    r.assign_and_confirm()


def test_medium():
    r = Runner("중간.csv", 4, "20262")
    assert word(r.apply(1, 1)) == "승인"
    assert word(r.apply(5, 1)) == "대기"
    assert word(r.vote(1, 5)) == "승인"
    assert word(r.apply(2, 1)) == "막힘"          # 주 역할 중복
    assert word(r.apply(6, 2)) == "승인"
    assert word(r.apply(9, 2)) == "대기"
    assert word(r.vote(6, 9)) == "승인"
    assert word(r.apply(10, 2)) == "대기"
    assert word(r.vote(6, 10)) == "대기"
    assert word(r.vote(9, 10, False)) == "거절"
    assert word(r.apply(3, 3)) == "승인"
    assert word(r.apply(11, 4)) == "승인"
    assert word(r.apply(12, 4)) == "대기"
    assert word(r.apply(12, 3)) == "막힘"         # 결과를 기다리는 지원이 있음
    assert word(service.close_recruiting()) == "승인"
    s = r.state()
    assert r.sid(3) not in s["member_team"] and r.sid(11) not in s["member_team"]
    assert s["latest_app"][r.sid(12)]["status"] == "거절"
    assert word(r.apply(2, 3)) == "막힘"          # 마감
    r.assign_and_confirm()


def test_hard():
    r = Runner("어려움.csv", 4, "20263")
    assert word(r.apply(1, 1)) == "승인"
    assert word(r.apply(4, 1)) == "대기"
    assert word(r.vote(1, 4)) == "승인"
    assert word(r.apply(7, 1)) == "대기"
    assert word(r.vote(1, 7)) == "대기"
    assert word(r.vote(4, 7)) == "승인"
    assert word(r.apply(2, 2)) == "승인"
    assert word(r.apply(8, 2)) == "대기"
    assert word(r.vote(2, 8)) == "승인"
    assert word(r.apply(5, 2)) == "대기"
    assert word(r.apply(6, 2)) == "대기"
    assert word(r.vote(2, 5)) == "대기"
    assert word(r.vote(8, 5)) == "승인"
    assert word(r.vote(2, 6)) == "대기"
    assert word(r.vote(8, 6)) == "대기"
    assert word(r.vote(5, 6)) == "거절"           # 승인 직전 검사
    assert word(r.apply(11, 3)) == "승인"
    assert word(r.apply(15, 3)) == "대기"
    assert word(r.vote(11, 15)) == "승인"
    assert word(r.apply(14, 3)) == "막힘"         # 필수 포함 역할 자리
    assert word(r.apply(10, 4)) == "승인"
    assert word(r.apply(13, 1)) == "대기"
    assert word(r.vote(1, 13)) == "대기"
    assert word(service.close_recruiting()) == "승인"
    s = r.state()
    assert s["latest_app"][r.sid(13)]["status"] == "거절"
    assert r.sid(10) not in s["member_team"]
    r.assign_and_confirm()
