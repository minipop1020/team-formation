"""Supabase(Postgres) 연결. 연결 풀을 한 번만 만들어 돌려쓰고, 끊긴 연결은 꺼낼 때 확인해 바꾼다."""
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv()

_pool = None


def get_secret(name):
    """배포 환경은 Streamlit Secrets, 내 PC는 .env 에서 값을 읽는다. 값은 화면에 출력하지 않는다."""
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name)


def get_pool():
    global _pool
    if _pool is None:
        url = get_secret("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL 이 설정되지 않음")
        _pool = ConnectionPool(
            url,
            min_size=1,
            max_size=5,
            open=True,
            # 꺼낼 때마다 살아 있는지 확인해, 앱이 잠들었다 깬 뒤의 죽은 연결을 버림
            check=ConnectionPool.check_connection,
            kwargs=dict(
                row_factory=dict_row,
                prepare_threshold=None,   # Supabase pooler 에서 prepared statement 충돌 방지
                connect_timeout=10,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
                tcp_user_timeout=8000,    # 끊긴 연결을 8초 안에 알아챔
            ),
        )
    return _pool


@contextmanager
def transaction(lock=False):
    """연결 하나를 빌려 트랜잭션으로 쓴다. lock=True 면 쓰기 동작끼리 한 줄로 세운다."""
    with get_pool().connection() as conn:
        with conn.transaction():
            if lock:
                conn.execute("select pg_advisory_xact_lock(20261102)")
            yield conn
