"""팀 규칙 검사와 배정 탐색. DB와 화면을 모르는 순수 함수만 둔다."""
from dataclasses import dataclass, field

ROLES = ["기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자"]
PL = "기획자"
WP = "웹 프로그래머"
MIN_SIZE = 2
MAX_SIZE = 4


@dataclass(frozen=True)
class Student:
    no: str
    name: str
    main: str
    subs: tuple = field(default_factory=tuple)

    def can(self, role):
        return self.main == role or role in self.subs


def required_filled(members):
    """기획자와 웹 프로그래머를 서로 다른 학생이 맡는다고 할 때 채울 수 있는 필수 역할 수(0~2)."""
    pls = [s for s in members if s.can(PL)]
    wps = [s for s in members if s.can(WP)]
    if any(a.no != b.no for a in pls for b in wps):
        return 2
    return 1 if (pls or wps) else 0


def missing_required(members):
    """아직 맡을 사람이 없는 필수 역할 이름 목록."""
    if required_filled(members) == 2:
        return []
    has_pl = any(s.can(PL) for s in members)
    has_wp = any(s.can(WP) for s in members)
    if has_pl and has_wp:
        # 한 학생만 둘 다 할 수 있는 경우: 하나는 비어 있음
        return [WP]
    return [r for r, ok in ((PL, has_pl), (WP, has_wp)) if not ok]


def join_block_reason(members, student):
    """student가 members 팀에 들어갈 수 없으면 이유를, 들어갈 수 있으면 None을 돌려준다."""
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m.main == student.main for m in members):
        return "같은 주 역할의 팀원이 있음"
    after = list(members) + [student]
    if MAX_SIZE - len(after) < 2 - required_filled(after):
        return "필수 포함 역할(기획자, 웹 프로그래머)을 맡을 학생의 자리가 모자람"
    return None


def team_errors(team_no, members):
    """확정한 팀 하나가 규칙을 어기는 점 목록."""
    errors = []
    if not MIN_SIZE <= len(members) <= MAX_SIZE:
        errors.append(f"팀{team_no}의 인원이 {len(members)}명임(2~4명이어야 함)")
    mains = [m.main for m in members]
    for role in sorted(set(mains)):
        if mains.count(role) > 1:
            errors.append(f"팀{team_no}에 주 역할 {role}이(가) {mains.count(role)}명임")
    for role in missing_required(members):
        errors.append(f"팀{team_no}에 {role}을(를) 맡을 학생이 없음")
    return errors


def validate_plan(teams, unassigned, plan):
    """배정안 검사 5가지.

    teams: {팀 번호: [Student]} 지금 팀 구성
    unassigned: [Student] 팀이 없는 학생
    plan: [(학번, 팀 번호)] 배정안
    돌려주는 값: 어긴 점 목록(비어 있으면 통과)
    """
    errors = []
    by_no = {s.no: s for s in unassigned}
    counted = {}
    for student_no, team_no in plan:
        counted[student_no] = counted.get(student_no, 0) + 1
        if student_no not in by_no:
            errors.append(f"팀이 없는 학생이 아닌 학번 {student_no}을(를) 배정함")
        if team_no not in teams:
            errors.append(f"없는 팀 번호 {team_no}에 배정함")
    # 1. 빠짐없이 배정
    for s in unassigned:
        if s.no not in counted:
            errors.append(f"{s.name}이(가) 배정되지 않음")
    # 2. 한 학생은 한 팀
    for student_no, n in counted.items():
        if n > 1:
            errors.append(f"학번 {student_no}이(가) {n}개 팀에 배정됨")
    if errors:
        return errors
    # 3~5. 인원, 주 역할 중복, 필수 포함 역할
    final = {no: list(members) for no, members in teams.items()}
    for student_no, team_no in plan:
        final[team_no].append(by_no[student_no])
    for team_no in sorted(final):
        errors.extend(team_errors(team_no, final[team_no]))
    return errors


def solve(teams, unassigned):
    """규칙을 모두 지키는 배정을 모든 경우를 따져 찾는다. 없으면 None."""
    team_nos = sorted(teams)
    current = {no: list(teams[no]) for no in team_nos}
    if any(team_errors(no, current[no]) and len(current[no]) >= MAX_SIZE for no in team_nos):
        return None

    # 갈 수 있는 팀이 적은 학생부터 놓아 가지를 줄임
    def options(s):
        return sum(1 for no in team_nos if join_block_reason(teams[no], s) is None)

    order = sorted(unassigned, key=options)
    plan = []

    def feasible_counts(remaining):
        # 남은 학생 수로 모든 팀을 2명 이상으로 만들 수 있는지
        need = sum(max(0, MIN_SIZE - len(current[no])) for no in team_nos)
        room = sum(MAX_SIZE - len(current[no]) for no in team_nos)
        return need <= remaining <= room

    def dfs(i):
        if not feasible_counts(len(order) - i):
            return False
        if i == len(order):
            return all(not team_errors(no, current[no]) for no in team_nos)
        s = order[i]
        for no in team_nos:
            if join_block_reason(current[no], s) is not None:
                continue
            current[no].append(s)
            plan.append((s.no, no))
            if dfs(i + 1):
                return True
            plan.pop()
            current[no].pop()
        return False

    return list(plan) if dfs(0) else None


def infeasible_reason(teams, unassigned):
    """규칙을 지키는 배정이 없을 때 그 이유를 사람이 읽을 문장으로 만든다."""
    reasons = []
    for no in sorted(teams):
        members = teams[no]
        missing = missing_required(members)
        if len(members) >= MAX_SIZE and missing:
            reasons.append(f"팀{no}은(는) 꽉 찼는데 {', '.join(missing)}을(를) 맡을 학생이 없음")
            continue
        candidates = [s for s in unassigned if all(m.main != s.main for m in members)]
        for role in missing:
            if not any(s.can(role) for s in candidates):
                reasons.append(f"팀{no}에 {role}을(를) 맡을 학생이 없음")
    need = sum(max(0, MIN_SIZE - len(m)) for m in teams.values())
    room = sum(MAX_SIZE - len(m) for m in teams.values())
    if len(unassigned) > room:
        reasons.append(f"팀이 없는 학생 {len(unassigned)}명을 넣을 자리가 {room}개뿐임")
    if len(unassigned) < need:
        reasons.append(f"모든 팀을 2명 이상으로 채우려면 {need}명이 필요한데 {len(unassigned)}명뿐임")
    if not reasons:
        reasons.append("인원, 주 역할 중복, 필수 포함 역할을 함께 지키는 배정 조합이 없음")
    return reasons
