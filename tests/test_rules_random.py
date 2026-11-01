"""무작위 상황에서 배정 탐색(solve)을 모든 배정을 하나씩 다 따져 보는 방법과 비교한다.

- solve 가 배정안을 내면: 그 배정안은 검사 5가지를 통과해야 함
- solve 가 None 이면: 정말로 규칙을 지키는 배정이 하나도 없어야 하고, 이유 문장이 있어야 함
"""
import itertools
import random

from tf.rules import (MAX_SIZE, ROLES, Student, infeasible_reason, join_block_reason, solve,
                      validate_plan)


def random_student(rng, no):
    main = rng.choice(ROLES)
    subs = tuple(rng.sample([r for r in ROLES if r != main], rng.randint(0, 2)))
    return Student(str(no), f"학생{no}", main, subs)


def random_situation(rng, n_unassigned):
    """지원 단계 규칙(join_block_reason)을 지키며 팀을 채운 뒤, 팀이 없는 학생을 만든다."""
    team_count = rng.choice([3, 4])
    teams = {no: [] for no in range(1, team_count + 1)}
    no = 1
    for _ in range(rng.randint(0, 3 * team_count)):
        s = random_student(rng, no)
        no += 1
        t = rng.choice(list(teams))
        if join_block_reason(teams[t], s) is None:
            teams[t].append(s)
    # 마감처럼 1명뿐인 팀의 학생은 뺀다
    unassigned = []
    for t, members in teams.items():
        if len(members) == 1:
            unassigned.extend(members)
            teams[t] = []
    while len(unassigned) < n_unassigned:
        unassigned.append(random_student(rng, no))
        no += 1
    return teams, unassigned[:n_unassigned]


def exists_valid_plan(teams, unassigned):
    team_nos = list(teams)
    for choice in itertools.product(team_nos, repeat=len(unassigned)):
        if any(sum(1 for c in choice if c == t) + len(teams[t]) > MAX_SIZE for t in team_nos):
            continue
        plan = [(s.no, t) for s, t in zip(unassigned, choice)]
        if not validate_plan(teams, unassigned, plan):
            return True
    return False


def check(rng):
    teams, unassigned = random_situation(rng, rng.randint(0, 6))
    plan = solve(teams, unassigned)
    if plan is not None:
        assert validate_plan(teams, unassigned, plan) == [], (teams, unassigned, plan)
    else:
        assert not exists_valid_plan(teams, unassigned), (teams, unassigned)
        assert infeasible_reason(teams, unassigned)
    return plan is not None


def test_solve_matches_exhaustive_search():
    rng = random.Random(20261102)
    results = [check(rng) for _ in range(400)]
    # 배정이 되는 경우와 안 되는 경우가 모두 시험되었는지
    assert any(results) and not all(results)


def test_solve_finds_plan_for_eight_students_quickly():
    rng = random.Random(8)
    for _ in range(30):
        teams, unassigned = random_situation(rng, 8)
        plan = solve(teams, unassigned)
        if plan is not None:
            assert validate_plan(teams, unassigned, plan) == []
