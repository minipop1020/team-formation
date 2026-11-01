"""팀 프로젝트 팀 편성 웹 앱. 화면 요소의 key 는 공고 2.7절 표를 그대로 따른다."""
import hashlib

import streamlit as st

from tf import ai, service
from tf.db import get_secret, transaction
from tf.rules import infeasible_reason, solve

st.set_page_config(page_title="팀 편성", page_icon="👥", layout="wide")

REFRESH_SECONDS = 3

# 크롬 자동 번역이 글자를 바꿔치기하면 3초마다 다시 그린 숫자(찬성 수 등)가 화면에 반영되지 않음.
# 페이지를 한국어로 표시하고 번역하지 말라고 알려 준다.
NO_TRANSLATE = """
<script>
const root = window.parent.document.documentElement;
root.setAttribute("lang", "ko");
root.setAttribute("translate", "no");
root.classList.add("notranslate");
const head = window.parent.document.head;
if (!head.querySelector('meta[name="google"]')) {
  const meta = window.parent.document.createElement("meta");
  meta.name = "google";
  meta.content = "notranslate";
  head.appendChild(meta);
}
</script>
"""


# ---------- 공통 ----------

@st.cache_resource
def admin_ready(fingerprint):
    """관리자 계정을 ADMIN_PASSWORD 로 만들거나 갱신한다(비밀번호가 바뀌면 fingerprint 가 바뀌어 다시 실행)."""
    with transaction(lock=True) as conn:
        service.ensure_admin(conn, get_secret("ADMIN_PASSWORD"))
    return True


def safe(action, *args):
    try:
        return action(*args)
    except Exception:
        return "막힘: DB 처리 중 오류가 났음. 잠시 뒤 다시 시도해 주세요"


def set_result(message):
    st.session_state.result = message


def show_result():
    with st.container(key="result"):
        message = st.session_state.get("result")
        if not message:
            return
        word = message.split(":")[0]
        {"승인": st.success, "대기": st.info, "거절": st.warning, "막힘": st.error}.get(word, st.write)(message)


def load_state():
    with transaction() as conn:
        return service.load_state(conn)


def describe(row):
    subs = ", ".join(row["subs"]) if row["subs"] else "없음"
    return f"{row['name']} · 주 역할 **{row['main_role']}** · 보조 역할 {subs}"


# ---------- 로그인 ----------

def on_login():
    student_no = st.session_state.get("login_student_no", "")
    password = st.session_state.get("login_password", "")
    try:
        with transaction() as conn:
            user = service.login(conn, student_no, password)
    except Exception:
        set_result("막힘: DB에 연결하지 못함. 잠시 뒤 다시 시도해 주세요")
        return
    if user:
        st.session_state.user = user
        set_result(f"승인: {user['name']}(으)로 로그인함")
    else:
        set_result("막힘: 학번이나 비밀번호가 틀림")


def on_logout():
    for key in ("user", "result", "plan", "plan_source", "assign_log"):
        st.session_state.pop(key, None)


def login_page():
    st.title("👥 팀 프로젝트 팀 편성")
    show_result()
    with st.container(border=True):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        st.button("로그인", key="login_submit", on_click=on_login, type="primary")


# ---------- 학생 화면 ----------

def on_apply(student_id, team_no):
    set_result(safe(service.apply, student_id, team_no))


def on_vote(voter_id, app_id, yes):
    set_result(safe(service.vote, voter_id, app_id, yes))


@st.fragment(run_every=REFRESH_SECONDS)
def student_view(uid):
    try:
        state = load_state()
    except Exception:
        st.error("DB에 연결하지 못함. 잠시 뒤 자동으로 다시 시도함")
        return
    show_result()
    if uid not in state["students"]:
        st.warning("명단이 바뀌어 이 계정이 없어졌음. 로그아웃하고 다시 로그인해 주세요")
        return

    my_team = state["member_team"].get(uid)
    my_pending = next((p for p in state["pending"] if p["student_id"] == uid), None)
    latest = state["latest_app"].get(uid)
    if my_team:
        st.markdown(f"#### 내 팀: 팀{my_team}")
    else:
        st.markdown("#### 아직 팀이 없음")
    if my_pending:
        st.caption(f"내 지원: 팀{my_pending['team_no']} 결과를 기다리는 중")
    elif latest and not my_team:
        st.caption(f"마지막 지원: 팀{latest['team_no']} {latest['status']} ({latest['reason'] or ''})")
    if state["closed"]:
        st.info("모집이 마감됨. 지원과 투표를 할 수 없음")
    can_apply = not (my_team or my_pending or state["closed"])

    students = state["students"]
    columns = st.columns(2)
    for i, team in enumerate(state["teams"]):
        with columns[i % 2]:
            with st.container(key=f"team_{team['no']}", border=True):
                mine = " · 내 팀" if team["no"] == my_team else ""
                st.markdown(f"### {team['name']} ({len(team['member_ids'])}/4){mine}")
                if not team["member_ids"]:
                    st.caption("팀원 없음")
                for sid in team["member_ids"]:
                    st.markdown("- " + describe(students[sid]))
                st.button(f"{team['name']}에 지원", key=f"apply_{team['no']}", disabled=not can_apply,
                          on_click=on_apply, args=(uid, team["no"]))
                apps = [p for p in state["pending"] if p["team_no"] == team["no"]]
                for app in apps:
                    applicant = students[app["student_id"]]
                    yes = sum(1 for v in app["votes"].values() if v == "찬성")
                    st.markdown(f"**지원자** {describe(applicant)} — 찬성 {yes}/{len(team['member_ids'])}")
                    if team["no"] != my_team or state["closed"]:
                        continue
                    if uid in app["votes"]:
                        st.caption(f"내 투표: {app['votes'][uid]}")
                        continue
                    c1, c2 = st.columns(2)
                    c1.button("찬성", key=f"vote_yes_{applicant['student_no']}",
                              on_click=on_vote, args=(uid, app["id"], True))
                    c2.button("거부", key=f"vote_no_{applicant['student_no']}",
                              on_click=on_vote, args=(uid, app["id"], False))


# ---------- 관리자 화면 ----------

def clear_plan():
    for key in ("plan", "plan_source", "assign_log"):
        st.session_state.pop(key, None)


def on_start():
    upload = st.session_state.get("admin_roster_file")
    if upload is None:
        set_result("막힘: 명단 CSV 파일을 먼저 올려야 함")
        return
    clear_plan()
    set_result(safe(service.reset_roster, upload.getvalue(), int(st.session_state.get("admin_team_count", 3))))


def on_close():
    clear_plan()
    set_result(safe(service.close_recruiting))


def on_confirm():
    plan = st.session_state.get("plan")
    if plan is None:
        set_result("막힘: 먼저 AI 배정으로 배정안을 받아야 함")
        return
    message = safe(service.confirm_plan, plan)
    set_result(message)
    if message.startswith("승인"):
        st.session_state.pop("plan", None)
        st.session_state.assign_log = st.session_state.get("assign_log", []) + ["배정안을 확정해 DB에 저장함"]


@st.fragment(run_every=REFRESH_SECONDS)
def admin_live():
    try:
        state = load_state()
    except Exception:
        st.error("DB에 연결하지 못함. 잠시 뒤 자동으로 다시 시도함")
        return
    status = "마감됨" if state["closed"] else "모집 중"
    if state["confirmed"]:
        status += " · 배정 확정됨"
    st.caption(f"상태: {status} · 학생 {len(state['students'])}명 · 팀 {len(state['teams'])}개")
    columns = st.columns(4)
    for i, team in enumerate(state["teams"]):
        with columns[i % 4]:
            with st.container(key=f"team_{team['no']}", border=True):
                st.markdown(f"**{team['name']}** ({len(team['member_ids'])}/4)")
                for sid in team["member_ids"]:
                    row = state["students"][sid]
                    st.markdown(f"- {row['name']} ({row['main_role']})")
                waiting = [p for p in state["pending"] if p["team_no"] == team["no"]]
                if waiting:
                    st.caption(f"대기 중인 지원 {len(waiting)}건")
    with st.container(key="unassigned", border=True):
        st.markdown("**팀이 없는 학생**")
        rows = [s for sid, s in state["students"].items() if sid not in state["member_team"]]
        if rows:
            for row in rows:
                st.markdown("- " + describe(row))
        else:
            st.caption("없음")


def run_assign(box):
    clear_plan()
    try:
        state = load_state()
    except Exception:
        set_result("막힘: DB에 연결하지 못함")
        return
    if not state["closed"]:
        set_result("막힘: 먼저 모집을 마감해야 함")
        return
    teams = service.teams_as_students(state)
    unassigned = service.unassigned_students(state)
    if solve(teams, unassigned) is None:
        st.session_state.assign_log = ["규칙을 지키는 배정이 없음"] + infeasible_reason(teams, unassigned)
        set_result("막힘: 규칙을 지키는 배정이 없음")
        return
    if not unassigned:
        st.session_state.plan = []
        st.session_state.plan_source = "팀이 없는 학생이 없음"
        st.session_state.assign_log = ["팀이 없는 학생이 없어 AI를 부르지 않음"]
        set_result("승인: 배정할 학생이 없음. 지금 구성을 확정할 수 있음")
        return

    log = []
    with box:
        waiting = st.empty()
        lines = st.empty()

    # 모델 이름은 코드 모양(`…`)으로 보여 줌. 그냥 쓰면 ':free:' 가 🆓 이모지로 바뀜
    def on_wait(attempt, model, elapsed):
        waiting.info(f"⏳ AI가 생각하는 중 · {attempt}차 모델 `{model}` 의 응답을 기다리는 중 "
                     f"({elapsed:.0f}초 / {ai.TOTAL_TIMEOUT}초)")

    def on_attempt(attempt, model, outcome, detail, elapsed):
        text = f"{attempt}차 모델 `{model}`: {outcome} ({elapsed:.1f}초)"
        if detail:
            text += f" — {detail[:200]}"
        log.append(text)
        lines.markdown("\n".join(f"- {line}" for line in log))
        service.log_ai(attempt, model, outcome, detail, round(elapsed, 1))

    plan, source = ai.assign(teams, unassigned, get_secret("OPENROUTER_API_KEY"), on_wait, on_attempt)
    # 진행 중에 보여 준 기록은 지움. 끝난 뒤 admin_page 가 assign_log 로 한 번만 다시 그림
    waiting.empty()
    lines.empty()
    st.session_state.plan = plan
    st.session_state.plan_source = source
    st.session_state.assign_log = log + [f"검사 5가지를 통과한 배정안: {source}"]
    set_result(f"승인: 배정안을 만듦({source}). 확인하고 확정해 주세요")


def admin_page():
    result_box = st.empty()

    st.subheader("1. 명단 올리기")
    c1, c2 = st.columns([3, 1])
    c1.file_uploader("명단 CSV (student_no,name,main_role,sub_roles)", type=["csv"], key="admin_roster_file")
    c2.number_input("팀 수", min_value=1, max_value=20, value=3, step=1, key="admin_team_count")
    st.button("명단으로 처음 상태 만들기", key="admin_start", on_click=on_start)

    st.subheader("2. 현황")
    admin_live()

    st.subheader("3. 모집 마감")
    st.button("모집 마감", key="admin_close", on_click=on_close)

    st.subheader("4. AI 배정과 확정")
    clicked = st.button("AI 배정", key="admin_assign")
    box = st.container(key="assign_status", border=True)
    if clicked:
        run_assign(box)
    with box:
        log = st.session_state.get("assign_log")
        if log:
            for line in log:
                st.markdown(f"- {line}")
        else:
            st.caption("마감한 뒤 AI 배정을 누르면 진행 상황이 여기에 보임")

    plan = st.session_state.get("plan")
    if plan:
        state = load_state()
        by_no = {s["student_no"]: s for s in state["students"].values()}
        st.markdown(f"**배정안** ({st.session_state.get('plan_source', '')})")
        st.table([{
            "이름": by_no[no]["name"],
            "주 역할": by_no[no]["main_role"],
            "보조 역할": ", ".join(by_no[no]["subs"]) or "없음",
            "배정된 팀": f"팀{team_no}",
        } for no, team_no in sorted(plan, key=lambda p: (p[1], p[0])) if no in by_no])
    st.button("배정안 확정", key="admin_confirm", on_click=on_confirm, disabled=plan is None, type="primary")

    with result_box.container():
        show_result()


# ---------- 시작 ----------

def main():
    st.html(NO_TRANSLATE, unsafe_allow_javascript=True)
    admin_password = get_secret("ADMIN_PASSWORD")
    if not admin_password or not get_secret("DATABASE_URL"):
        st.error("비밀 값(DATABASE_URL, ADMIN_PASSWORD)이 설정되지 않음")
        return
    try:
        admin_ready(hashlib.sha256(admin_password.encode()).hexdigest())
    except Exception:
        st.error("DB에 연결하지 못함. 잠시 뒤 새로 고침해 주세요")
        return

    user = st.session_state.get("user")
    if not user:
        login_page()
        return
    top1, top2 = st.columns([5, 1])
    with top1:
        with st.container(key="whoami"):
            role = "관리자로" if user["is_admin"] else "학생으로"
            st.markdown(f"**{user['name']}** ({user['no']}) · {role} 로그인함")
    top2.button("로그아웃", key="logout", on_click=on_logout)
    if user["is_admin"]:
        admin_page()
    else:
        student_view(user["id"])


main()
