"""공개되지 않은 채점 시나리오를 흉내 낸 무작위 시나리오를 실제 DB로 돌린다.

- 학생 8~16명, 팀 3~4개, 역할과 지원·투표 순서를 무작위로 만든다(공고 6.2절 8항)
- 공고 2.3~2.5절을 앱 코드와 따로 구현한 '기준 모델'의 예상과 앱의 결과 안내 첫 단어를 매 단계 비교한다
- 매 단계 DB 상태가 규칙(정원, 주 역할 중복, 필수 포함 역할 자리)을 지키는지 검사한다
- 모든 작업은 하나의 트랜잭션 안에서 하고 끝에 되돌려, 실제 DB 데이터는 바꾸지 않는다

돌리는 횟수: 환경 변수 TF_RANDOM_RUNS (기본 3)
"""
import os
import random
from contextlib import contextmanager

import pytest

from tf import db, service
from tf.db import get_secret
from tf.rules import infeasible_reason, solve, team_errors, validate_plan
from test_rules_random import exists_valid_plan

pytestmark = pytest.mark.skipif(not (get_secret("DATABASE_URL") or "").startswith("postgres"),
                                reason="DATABASE_URL 없음")

ROLES = ["기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자"]


# ---------- 기준 모델 (공고 규칙을 앱 코드와 따로 구현) ----------

def can(s, role):
    return s["main"] == role or role in s["subs"]


def filled(members):
    """기획자와 웹 프로그래머를 서로 다른 사람이 맡을 때 채운 필수 역할 수."""
    for a in members:
        for b in members:
            if a is not b and can(a, "기획자") and can(b, "웹 프로그래머"):
                return 2
    return 1 if any(can(m, "기획자") or can(m, "웹 프로그래머") for m in members) else 0


def blocked(members, s):
    if len(members) >= 4:
        return True
    if any(m["main"] == s["main"] for m in members):
        return True
    after = members + [s]
    return 4 - len(after) < 2 - filled(after)


class Model:
    def __init__(self, students, team_count):
        self.students = students                       # 학번 → 학생
        self.teams = {t: [] for t in range(1, team_count + 1)}
        self.pending = {}                              # 지원자 학번 → {"team": t, "votes": {학번: bool}}
        self.closed = False

    def team_of(self, no):
        return next((t for t, ms in self.teams.items() if any(m["no"] == no for m in ms)), None)

    def apply(self, no, t):
        s = self.students[no]
        if self.closed or self.team_of(no) or no in self.pending or blocked(self.teams[t], s):
            return "막힘"
        if not self.teams[t]:
            self.teams[t].append(s)
            return "승인"
        self.pending[no] = {"team": t, "votes": {}}
        return "대기"

    def vote(self, voter, applicant, yes):
        p = self.pending[applicant]
        p["votes"][voter] = yes
        if not yes:
            del self.pending[applicant]
            return "거절"
        members = self.teams[p["team"]]
        if all(p["votes"].get(m["no"]) for m in members):
            del self.pending[applicant]
            if blocked(members, self.students[applicant]):
                return "거절"
            members.append(self.students[applicant])
            return "승인"
        return "대기"

    def close(self):
        self.closed = True
        self.pending.clear()
        for t, ms in self.teams.items():
            if len(ms) == 1:
                self.teams[t] = []


# ---------- 실행 ----------

@pytest.fixture
def rollback(monkeypatch):
    with db.get_pool().connection() as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def same_conn(lock=False):
                with conn.transaction():
                    if lock:
                        conn.execute("select pg_advisory_xact_lock(20261102)")
                    yield conn

            monkeypatch.setattr(service, "transaction", same_conn)
            yield conn


def make_roster(rng, run, team_count, realistic):
    """realistic 이면 채점 시나리오처럼 팀마다 기획자와 웹 프로그래머(주 역할)가 한 명씩은 있게 만든다."""
    # 채점 시나리오는 모든 학생을 배정하므로 학생 수는 팀 수 × 4 이하
    n = rng.randint(max(8, 2 * team_count), min(16, 4 * team_count))
    fixed = ["기획자", "웹 프로그래머"] * team_count if realistic else []
    students = {}
    for i in range(1, n + 1):
        main = fixed[i - 1] if i <= len(fixed) else rng.choice(ROLES)
        subs = rng.sample([r for r in ROLES if r != main], rng.randint(0, 2))
        no = f"R{run}{i:02d}"
        students[no] = {"no": no, "name": f"무작위{run}-{i}", "main": main, "subs": subs}
    lines = ["student_no,name,main_role,sub_roles"]
    lines += [f"{s['no']},{s['name']},{s['main']},{';'.join(s['subs'])}" for s in students.values()]
    return students, ("﻿" + "\n".join(lines) + "\n").encode("utf-8")


def check_db_invariants(conn, where):
    state = service.load_state(conn)
    for no, members in service.teams_as_students(state).items():
        assert len(members) <= 4, f"{where}: 팀{no} 인원 초과"
        mains = [m.main for m in members]
        assert len(mains) == len(set(mains)), f"{where}: 팀{no} 주 역할 중복"
        # 필수 포함 역할 자리: 남은 자리로 아직 못 채운 필수 역할을 채울 수 있어야 함
        need = [e for e in team_errors(no, members) if "맡을 학생이 없음" in e]
        if members:
            assert 4 - len(members) >= len(need), f"{where}: 팀{no} 필수 역할 자리 모자람"
    return state


def run_one(conn, seed, realistic):
    rng = random.Random(seed)
    team_count = rng.choice([3, 4])
    students, csv = make_roster(rng, seed, team_count, realistic)
    assert service.reset_roster(csv, team_count).startswith("승인")
    ids = {s["student_no"]: sid for sid, s in service.load_state(conn)["students"].items()}
    model = Model(students, team_count)
    log = []

    for step in range(rng.randint(15, 30)):
        state = service.load_state(conn)
        pending_apps = [p for p in state["pending"]]
        if pending_apps and rng.random() < 0.5:
            p = rng.choice(pending_apps)
            team = next(t for t in state["teams"] if t["no"] == p["team_no"])
            voters = [sid for sid in team["member_ids"] if sid not in p["votes"]]
            voter_id = rng.choice(voters)
            applicant = state["students"][p["student_id"]]["student_no"]
            voter = state["students"][voter_id]["student_no"]
            yes = rng.random() < 0.8
            got = service.vote(voter_id, p["id"], yes).split(":")[0]
            want = model.vote(voter, applicant, yes)
            action = f"{step}: {voter} 이(가) {applicant} 에 {'찬성' if yes else '거부'}"
        else:
            no = rng.choice(list(students))
            t = rng.randint(1, team_count)
            got = service.apply(ids[no], t).split(":")[0]
            want = model.apply(no, t)
            action = f"{step}: {no}({students[no]['main']}) 이(가) 팀{t} 에 지원"
        log.append(f"{action} → 앱 {got} / 기준 {want}")
        assert got == want, "\n".join(log)
        check_db_invariants(conn, action)

    assert service.close_recruiting().startswith("승인")
    model.close()
    state = check_db_invariants(conn, "마감 뒤")
    assert not state["pending"], "마감 뒤 대기 지원이 남음"
    db_teams = {t["no"]: sorted(state["students"][sid]["student_no"] for sid in t["member_ids"]) for t in state["teams"]}
    model_teams = {t: sorted(m["no"] for m in ms) for t, ms in model.teams.items()}
    assert db_teams == model_teams, f"마감 뒤 팀 구성이 다름\n앱 {db_teams}\n기준 {model_teams}\n" + "\n".join(log)

    teams = service.teams_as_students(state)
    unassigned = service.unassigned_students(state)
    if len(unassigned) > 8:
        return f"seed {seed}: 학생 {len(students)}명, 팀 {team_count}개, 단계 {len(log)}개 통과, 팀 없는 학생 {len(unassigned)}명(8명 초과라 배정 생략)"
    plan = solve(teams, unassigned)
    if plan is None:
        assert infeasible_reason(teams, unassigned)
        # 앱의 판단을 믿지 않고, 가능한 배정을 하나하나 다 따져 정말 없는지 확인
        assert not exists_valid_plan(teams, unassigned), "배정이 있는데 앱이 없다고 함"
        return f"seed {seed}: 학생 {len(students)}명, 팀 {team_count}개, 단계 {len(log)}개 통과, 배정 불가(이유 표시) 확인"
    assert validate_plan(teams, unassigned, plan) == []
    assert service.confirm_plan(plan).startswith("승인")
    final = service.load_state(conn)
    assert not service.unassigned_students(final)
    for no, members in service.teams_as_students(final).items():
        assert team_errors(no, members) == [], f"확정 뒤 팀{no} 규칙 위반"
    return f"seed {seed}: 학생 {len(students)}명, 팀 {team_count}개, 단계 {len(log)}개 통과, 확정 {len(plan)}명 규칙 통과"


def test_random_scenarios(rollback, capsys):
    runs = int(os.getenv("TF_RANDOM_RUNS", "3"))
    start = int(os.getenv("TF_RANDOM_START", "1"))
    # 짝수 seed 는 채점 시나리오처럼 배정이 되도록 만든 명단, 홀수 seed 는 역할을 완전히 무작위로
    for seed in range(start, start + runs):
        summary = run_one(rollback, seed, realistic=seed % 2 == 0)
        with capsys.disabled():
            print("\n" + summary)
