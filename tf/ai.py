"""AI 배정. 1차 → 2차 → 3차 모델 순으로 부르고, 모두 실패하면 앱이 모든 경우를 따져 배정한다."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from tf.rules import solve, validate_plan

# 2026-11-02 어려움 시나리오 마감 뒤 상태로 시험해 고름
# (1차 37초 통과, 2차 74초 통과. 3차 gemma 는 시험 때 429 였으나 생각 단계가 없어 실패해도 바로 넘어감)
MODELS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "dots-studio/dots-3-note-preview:free",
    "google/gemma-4-31b-it:free",
]
# 생각하는 모델은 답 전에 수천 토큰을 씀. 6000 이면 생각만 하다 빈 응답이 났음.
# 1차 모델은 초당 약 180토큰이라 120초면 약 21,700토큰을 씀. 그만큼은 다 쓸 수 있게 하고,
# 3차 gemma 의 최대 출력(32,768)은 넘지 않게 함
MAX_TOKENS = 24000
TOTAL_TIMEOUT = 120  # 모델 하나를 부른 때부터 응답을 받을 때까지의 전체 대기 시간(초)
URL = "https://openrouter.ai/api/v1/chat/completions"


class AIFailure(Exception):
    pass


def build_messages(teams, unassigned):
    def describe(s):
        return {"student_no": s.no, "name": s.name, "main_role": s.main, "sub_roles": list(s.subs)}

    data = {
        "teams": [{"team": no, "members": [describe(s) for s in members]} for no, members in sorted(teams.items())],
        "unassigned": [describe(s) for s in unassigned],
    }
    rules = (
        "팀이 없는 학생(unassigned)을 팀에 배정하라. 이미 팀에 있는 학생은 옮기지 않는다.\n"
        "규칙:\n"
        "1. 팀이 없는 학생은 빠짐없이, 한 학생은 한 팀에만 배정한다.\n"
        "2. 배정 뒤 모든 팀은 2~4명이어야 한다.\n"
        "3. 한 팀 안에 주 역할(main_role)이 같은 학생이 2명 이상이면 안 된다.\n"
        "4. 모든 팀에는 '기획자'를 맡을 학생과 '웹 프로그래머'를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 한다. "
        "주 역할이나 보조 역할(sub_roles)로 맡을 수 있다.\n"
        '답은 설명 없이 JSON 하나로만 한다: {"assignments": [{"student_no": "학번", "team": 팀번호}]}'
    )
    return [
        {"role": "system", "content": "너는 팀 편성 규칙을 정확히 지키는 배정 도우미다."},
        {"role": "user", "content": rules + "\n\n지금 상태:\n" + json.dumps(data, ensure_ascii=False)},
    ]


def call_model(model, messages, api_key):
    resp = requests.post(
        URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.2,
            "reasoning": {"effort": "low"},
        },
        timeout=(10, TOTAL_TIMEOUT),
    )
    if resp.status_code != 200:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            msg = resp.text[:200]
        raise AIFailure(f"HTTP {resp.status_code} {msg}".strip())
    try:
        data = resp.json()
    except ValueError:
        raise AIFailure("응답이 JSON이 아님")
    if "error" in data:
        # 200 응답 안에 제공자 오류가 담겨 오기도 함
        raise AIFailure(f"제공자 오류 {data['error'].get('message', '')}".strip())
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, AttributeError):
        raise AIFailure("응답 형식이 올바르지 않음")
    if not content.strip():
        raise AIFailure("빈 응답")
    return content


def parse_plan(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise AIFailure("응답에 JSON이 없음")
    try:
        data = json.loads(match.group(0))
        plan = []
        for item in data["assignments"]:
            team = int(re.sub(r"\D", "", str(item["team"])))
            plan.append((str(item["student_no"]).strip(), team))
        return plan
    except (ValueError, KeyError, TypeError):
        raise AIFailure("배정안 JSON을 읽지 못함")


def call_with_deadline(model, messages, api_key, on_wait):
    """모델 하나를 부르고, 응답이 늦으면 TOTAL_TIMEOUT 초에서 끊는다. on_wait(경과 초)로 대기 화면을 갱신한다."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(call_model, model, messages, api_key)
    start = time.monotonic()
    try:
        while True:
            elapsed = time.monotonic() - start
            if future.done():
                return future.result()
            if elapsed >= TOTAL_TIMEOUT:
                raise AIFailure(f"{TOTAL_TIMEOUT}초 안에 응답하지 않음")
            on_wait(elapsed)
            time.sleep(0.5)
    finally:
        # 늦은 요청은 기다리지 않고 버림
        pool.shutdown(wait=False, cancel_futures=True)


def assign(teams, unassigned, api_key, on_wait, on_attempt):
    """배정안을 만든다.

    on_wait(차수, 모델, 경과 초): 대기 화면 갱신
    on_attempt(차수, 모델, 결과, 자세한 내용, 경과 초): 시도마다 한 번
    돌려주는 값: (배정안, 출처 설명)
    """
    messages = build_messages(teams, unassigned)
    for attempt, model in enumerate(MODELS, start=1):
        start = time.monotonic()
        try:
            if not api_key:
                raise AIFailure("OPENROUTER_API_KEY 가 설정되지 않음")
            text = call_with_deadline(model, messages, api_key, lambda e: on_wait(attempt, model, e))
            plan = parse_plan(text)
            errors = validate_plan(teams, unassigned, plan)
            if errors:
                on_attempt(attempt, model, "검사 실패", "; ".join(errors), time.monotonic() - start)
                continue
            on_attempt(attempt, model, "성공", "", time.monotonic() - start)
            return plan, f"{attempt}차 모델({model})의 배정안"
        except AIFailure as e:
            outcome = "시간 초과" if "응답하지 않음" in str(e) else ("빈 응답" if "빈 응답" in str(e) else "오류")
            on_attempt(attempt, model, outcome, str(e), time.monotonic() - start)
        except requests.RequestException as e:
            on_attempt(attempt, model, "오류", type(e).__name__, time.monotonic() - start)
    plan = solve(teams, unassigned)
    return plan, "3차 모델까지 실패해 앱이 모든 경우를 따져 찾은 배정안"
