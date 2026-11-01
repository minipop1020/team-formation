"""앱의 동작(로그인, 명단 올리기, 지원, 투표, 마감, 확정). 결과 안내는 승인/대기/거절/막힘 가운데 하나로 시작한다."""
import csv
import io

import bcrypt

from tf.db import transaction
from tf.rules import ROLES, Student, join_block_reason, validate_plan

STUDENT_PASSWORD = "1234"


def _hash(password, rounds=10):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


# ---------- 상태 읽기 ----------

# DB가 멀어도 화면이 느려지지 않게, 화면에 필요한 상태를 질의 한 번으로 읽는다
STATE_SQL = """
select json_build_object(
  'students', (select coalesce(json_agg(x order by x.student_no), '[]') from (
      select s.id, s.student_no, s.name, s.main_role, s.is_admin,
             coalesce((select json_agg(r.role order by r.role) from tf_student_sub_roles r
                       where r.student_id = s.id), '[]') as subs
      from tf_students s) x),
  'teams', (select coalesce(json_agg(t order by t.no), '[]') from (select id, no, name from tf_teams) t),
  'members', (select coalesce(json_agg(m order by m.slot_no), '[]') from tf_members m),
  'pending', (select coalesce(json_agg(p order by p.created_at, p.id), '[]') from (
      select a.id, a.student_id, a.created_at, t.no as team_no
      from tf_applications a join tf_teams t on t.id = a.team_id where a.status = '대기') p),
  'votes', (select coalesce(json_agg(v), '[]') from (
      select v.application_id, v.voter_id, v.vote from tf_votes v
      join tf_applications a on a.id = v.application_id where a.status = '대기') v),
  'latest', (select coalesce(json_agg(l), '[]') from (
      select distinct on (a.student_id) a.student_id, a.status, a.reason, t.no as team_no
      from tf_applications a join tf_teams t on t.id = a.team_id
      order by a.student_id, a.created_at desc, a.id desc) l),
  'settings', (select coalesce(json_object_agg(key, value), '{}') from tf_settings)
) as state
"""


def load_state(conn):
    data = conn.execute(STATE_SQL).fetchone()["state"]
    students = {r["id"]: r for r in data["students"] if not r["is_admin"]}
    team_by_id = {t["id"]: dict(t, member_ids=[]) for t in data["teams"]}
    member_team = {}
    for m in data["members"]:
        team_by_id[m["team_id"]]["member_ids"].append(m["student_id"])
        member_team[m["student_id"]] = team_by_id[m["team_id"]]["no"]
    pending = [dict(p, votes={}) for p in data["pending"]]
    by_app = {p["id"]: p for p in pending}
    for v in data["votes"]:
        by_app[v["application_id"]]["votes"][v["voter_id"]] = v["vote"]
    latest = data["latest"]
    settings = data["settings"]
    teams = data["teams"]
    return {
        "students": students,
        "teams": [team_by_id[t["id"]] for t in teams],
        "member_team": member_team,
        "pending": pending,
        "latest_app": {r["student_id"]: r for r in latest},
        "closed": settings.get("closed") == "1",
        "confirmed": settings.get("confirmed") == "1",
    }


def to_student(row):
    return Student(row["student_no"], row["name"], row["main_role"], tuple(row["subs"]))


def team_members(state, team_no):
    team = next(t for t in state["teams"] if t["no"] == team_no)
    return [to_student(state["students"][sid]) for sid in team["member_ids"]]


def unassigned_students(state):
    return [to_student(s) for sid, s in state["students"].items() if sid not in state["member_team"]]


def teams_as_students(state):
    return {t["no"]: team_members(state, t["no"]) for t in state["teams"]}


# ---------- 로그인 ----------

def ensure_admin(conn, password):
    """앱이 시작할 때 관리자 계정을 만들거나 비밀번호를 갱신한다."""
    row = conn.execute("select id, password_hash from tf_students where student_no = 'admin'").fetchone()
    if row is None:
        conn.execute("""insert into tf_students (student_no, name, main_role, password_hash, is_admin)
                        values ('admin', '관리자', '관리자', %s, true)""", (_hash(password, 12),))
    elif not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        conn.execute("update tf_students set password_hash = %s where id = %s", (_hash(password, 12), row["id"]))


def login(conn, student_no, password):
    row = conn.execute("select id, student_no, name, password_hash, is_admin from tf_students where student_no = %s",
                       (student_no.strip(),)).fetchone()
    if row and password and bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return {"id": row["id"], "no": row["student_no"], "name": row["name"], "is_admin": row["is_admin"]}
    return None


# ---------- 명단 올리기 ----------

def parse_roster(data):
    """명단 CSV(BOM 있는 UTF-8 도 됨)를 읽어 [Student] 로 돌려준다. 틀리면 ValueError."""
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    need = {"student_no", "name", "main_role", "sub_roles"}
    if not reader.fieldnames or not need <= {f.strip() for f in reader.fieldnames}:
        raise ValueError("열 이름이 student_no,name,main_role,sub_roles 가 아님")
    students, seen = [], set()
    for i, row in enumerate(reader, start=2):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        if not row["student_no"]:
            continue
        no, main = row["student_no"], row["main_role"]
        subs = tuple(r.strip() for r in row["sub_roles"].split(";") if r.strip())
        if no in seen or no == "admin":
            raise ValueError(f"{i}번째 줄: 학번 {no} 이(가) 겹침")
        if main not in ROLES or any(r not in ROLES for r in subs):
            raise ValueError(f"{i}번째 줄: 6개 역할 밖의 값이 있음")
        if len(subs) > 2:
            raise ValueError(f"{i}번째 줄: 보조 역할이 2개보다 많음")
        seen.add(no)
        students.append(Student(no, row["name"], main, subs))
    if not students:
        raise ValueError("학생이 한 명도 없음")
    return students


def reset_roster(data, team_count):
    try:
        students = parse_roster(data)
    except (ValueError, UnicodeDecodeError) as e:
        return f"막힘: 명단 CSV를 읽지 못함({e})"
    if not 1 <= team_count <= 20:
        return "막힘: 팀 수는 1~20 이어야 함"
    hashes = [_hash(STUDENT_PASSWORD) for _ in students]
    with transaction(lock=True) as conn:
        conn.execute("delete from tf_votes")
        conn.execute("delete from tf_applications")
        conn.execute("delete from tf_members")
        conn.execute("delete from tf_teams")
        conn.execute("delete from tf_students where not is_admin")
        conn.execute("delete from tf_settings where key in ('closed', 'confirmed')")
        with conn.cursor() as cur:
            cur.executemany("insert into tf_teams (no, name) values (%s, %s)",
                            [(n, f"팀{n}") for n in range(1, team_count + 1)])
            for s, h in zip(students, hashes):
                cur.execute("""insert into tf_students (student_no, name, main_role, password_hash)
                               values (%s, %s, %s, %s) returning id""", (s.no, s.name, s.main, h))
                sid = cur.fetchone()["id"]
                for role in s.subs:
                    cur.execute("insert into tf_student_sub_roles (student_id, role) values (%s, %s)", (sid, role))
    return f"승인: 학생 {len(students)}명과 팀 {team_count}개로 처음 상태를 만듦"


# ---------- 지원과 투표 ----------

def _insert_member(conn, team_id, student_id):
    used = {r["slot_no"] for r in conn.execute("select slot_no from tf_members where team_id = %s", (team_id,))}
    slot = min(set(range(1, 5)) - used)
    conn.execute("insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
                 (team_id, slot, student_id))


def apply(student_id, team_no):
    with transaction(lock=True) as conn:
        state = load_state(conn)
        me = state["students"].get(student_id)
        team = next((t for t in state["teams"] if t["no"] == team_no), None)
        if me is None or team is None:
            return "막힘: 학생이나 팀을 찾을 수 없음"
        if state["closed"]:
            return "막힘: 모집이 마감됨"
        if student_id in state["member_team"]:
            return "막힘: 이미 팀이 있음"
        if any(p["student_id"] == student_id for p in state["pending"]):
            return "막힘: 결과를 기다리는 지원이 있음"
        members = team_members(state, team_no)
        reason = join_block_reason(members, to_student(me))
        if reason:
            return f"막힘: {reason}"
        if not members:
            _insert_member(conn, team["id"], student_id)
            conn.execute("""insert into tf_applications (team_id, student_id, status, reason, decided_at)
                            values (%s, %s, '승인', '빈 팀', now())""", (team["id"], student_id))
            return f"승인: {team['name']}에 들어감(빈 팀이라 바로 승인)"
        conn.execute("insert into tf_applications (team_id, student_id) values (%s, %s)", (team["id"], student_id))
        return f"대기: {team['name']} 팀원 {len(members)}명의 투표를 기다림"


def _decide(conn, app_id, status, reason):
    conn.execute("update tf_applications set status = %s, reason = %s, decided_at = now() where id = %s",
                 (status, reason, app_id))


def vote(voter_id, app_id, yes):
    with transaction(lock=True) as conn:
        app = conn.execute("""select a.id, a.status, a.student_id, a.team_id, t.no as team_no, t.name as team_name
                              from tf_applications a join tf_teams t on t.id = a.team_id
                              where a.id = %s""", (app_id,)).fetchone()
        if app is None:
            return "막힘: 지원을 찾을 수 없음"
        if app["status"] != "대기":
            return f"{app['status']}: 이미 결과가 나온 지원임"
        state = load_state(conn)
        if state["closed"]:
            return "막힘: 모집이 마감됨"
        team = next(t for t in state["teams"] if t["id"] == app["team_id"])
        if voter_id not in team["member_ids"]:
            return "막힘: 이 팀의 팀원만 투표할 수 있음"
        applicant = state["students"][app["student_id"]]
        voter = state["students"][voter_id]
        pending = next(p for p in state["pending"] if p["id"] == app_id)
        if voter_id in pending["votes"]:
            return f"막힘: 이미 투표함(내 투표: {pending['votes'][voter_id]})"
        conn.execute("insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
                     (app_id, voter_id, "찬성" if yes else "거부"))
        if not yes:
            _decide(conn, app_id, "거절", f"{voter['name']} 거부")
            return f"거절: {voter['name']}이(가) {applicant['name']}의 지원을 거부함"
        pending["votes"][voter_id] = "찬성"
        yes_count = sum(1 for sid in team["member_ids"] if pending["votes"].get(sid) == "찬성")
        if yes_count < len(team["member_ids"]):
            return f"대기: 팀원 {len(team['member_ids'])}명 가운데 {yes_count}명이 찬성함"
        # 승인 직전 검사: 앞의 지원이 먼저 승인되어 팀이 바뀌었을 수 있음
        reason = join_block_reason(team_members(state, app["team_no"]), to_student(applicant))
        if reason:
            _decide(conn, app_id, "거절", f"승인 직전 검사: {reason}")
            return f"거절: 승인 직전 검사에서 규칙을 어김({reason})"
        _insert_member(conn, app["team_id"], app["student_id"])
        _decide(conn, app_id, "승인", "팀원 모두 찬성")
        return f"승인: {applicant['name']}이(가) {app['team_name']}의 팀원이 됨"


# ---------- 마감과 확정 ----------

def close_recruiting():
    with transaction(lock=True) as conn:
        state = load_state(conn)
        if not state["teams"]:
            return "막힘: 먼저 명단을 올려야 함"
        if state["closed"]:
            return "막힘: 이미 모집을 마감함"
        conn.execute("""insert into tf_settings (key, value) values ('closed', '1')
                        on conflict (key) do update set value = '1'""")
        rejected = conn.execute("""update tf_applications set status = '거절', reason = '모집 마감', decided_at = now()
                                   where status = '대기'""").rowcount
        removed = conn.execute("""delete from tf_members where team_id in
                                  (select team_id from tf_members group by team_id having count(*) = 1)""").rowcount
        return f"승인: 모집을 마감함(대기 지원 {rejected}건 거절, 혼자인 학생 {removed}명을 팀에서 뺌)"


def confirm_plan(plan):
    with transaction(lock=True) as conn:
        state = load_state(conn)
        if not state["closed"]:
            return "막힘: 모집을 마감한 뒤에 확정할 수 있음"
        errors = validate_plan(teams_as_students(state), unassigned_students(state), plan)
        if errors:
            return "막힘: 배정안이 지금 상태와 맞지 않음(" + "; ".join(errors) + ")"
        ids = {s["student_no"]: sid for sid, s in state["students"].items()}
        team_ids = {t["no"]: t["id"] for t in state["teams"]}
        for student_no, team_no in plan:
            _insert_member(conn, team_ids[team_no], ids[student_no])
        conn.execute("""insert into tf_settings (key, value) values ('confirmed', '1')
                        on conflict (key) do update set value = '1'""")
        return f"승인: 배정안을 확정함({len(plan)}명 배정)"


def log_ai(attempt, model, outcome, detail, elapsed):
    try:
        with transaction() as conn:
            conn.execute("""insert into tf_ai_logs (attempt, model, outcome, detail, elapsed_s)
                            values (%s, %s, %s, %s, %s)""", (attempt, model, outcome, (detail or "")[:500], elapsed))
    except Exception:
        pass
