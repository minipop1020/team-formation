from tf.rules import (Student, join_block_reason, required_filled, solve,
                      validate_plan, infeasible_reason)

PL, WP, FD, TT, RW, PT = "기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자"


def S(no, main, *subs):
    return Student(str(no), f"학생{no}", main, tuple(subs))


def test_required_filled_counts_distinct_people():
    assert required_filled([S(1, PL, WP)]) == 1
    assert required_filled([S(1, PL, WP), S(2, TT, WP)]) == 2
    assert required_filled([S(1, TT), S(2, PT)]) == 0


def test_block_reasons():
    team = [S(1, PL, RW), S(2, WP, PL)]
    assert join_block_reason(team, S(3, PL)) == "같은 주 역할의 팀원이 있음"
    assert join_block_reason(team + [S(3, FD), S(4, TT)], S(5, RW)) == "팀이 꽉 참"
    # 공고 예: 테스터와 발표자만 있는 팀에 보고서 작성자가 지원하면 막음
    assert "자리가 모자람" in join_block_reason([S(11, TT, PT), S(15, PT, FD)], S(14, RW, TT))
    assert join_block_reason([S(11, TT, PT)], S(14, RW, TT)) is None


def test_hard_practice_final_assignment():
    teams = {
        1: [S(1, PL, RW), S(4, WP, PL), S(7, FD, PT, TT)],
        2: [S(2, PL, PT), S(8, FD, TT), S(5, WP, FD)],
        3: [S(11, TT, PT), S(15, PT, FD)],
        4: [],
    }
    unassigned = [S(3, PL, TT), S(6, WP, RW), S(9, FD, WP), S(10, TT, RW),
                  S(12, TT, PL), S(13, RW, PT, FD), S(14, RW, TT), S(16, PT)]
    plan = solve(teams, unassigned)
    assert plan is not None
    assert validate_plan(teams, unassigned, plan) == []


def test_validate_plan_catches_missing_and_duplicates():
    teams = {1: [S(1, PL)], 2: [S(2, WP)]}
    unassigned = [S(3, WP), S(4, PL)]
    assert validate_plan(teams, unassigned, [("3", 1), ("4", 2)]) == []
    errors = validate_plan(teams, unassigned, [("3", 1)])
    assert any("배정되지 않음" in e for e in errors)
    errors = validate_plan(teams, unassigned, [("3", 2), ("4", 1)])
    assert any("주 역할" in e for e in errors)


def test_infeasible_full_team_without_wp():
    teams = {1: [S(1, PL), S(2, TT), S(3, RW), S(4, PT)], 2: [S(5, WP)]}
    unassigned = [S(6, PL)]
    assert solve(teams, unassigned) is None
    reasons = infeasible_reason(teams, unassigned)
    assert any("팀1" in r and WP in r for r in reasons)
