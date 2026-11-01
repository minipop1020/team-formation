-- 팀 편성 웹 앱의 테이블. 6장 실습 테이블과 구분하려고 모두 tf_ 로 시작함

create table if not exists tf_students (
    id            bigserial primary key,
    student_no    text not null unique,
    name          text not null,
    main_role     text check (main_role in             -- 6개 역할 밖의 값을 막음. 관리자는 역할이 없어 비워 둠
                    ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    password_hash text not null,
    is_admin      boolean not null default false,
    constraint tf_students_role_required check (is_admin or main_role is not null)
);

create table if not exists tf_student_sub_roles (
    student_id bigint not null references tf_students(id) on delete cascade,
    role       text not null check (role in
                 ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    primary key (student_id, role)
);

create table if not exists tf_teams (
    id   bigserial primary key,
    no   int not null unique,          -- 팀1, 팀2의 번호 (화면 key team_1 의 1)
    name text not null unique
);

-- (team_id, slot_no) 기본 키와 student_id 유일성 제약이
-- "마지막 자리에 먼저 도착한 학생만 기록"과 "한 학생은 한 팀"을 DB에서 지켜 줌
create table if not exists tf_members (
    team_id    bigint not null references tf_teams(id) on delete cascade,
    slot_no    int not null check (slot_no between 1 and 4),
    student_id bigint not null unique references tf_students(id) on delete cascade,
    primary key (team_id, slot_no)
);

create table if not exists tf_applications (
    id         bigserial primary key,
    team_id    bigint not null references tf_teams(id) on delete cascade,
    student_id bigint not null references tf_students(id) on delete cascade,
    status     text not null default '대기' check (status in ('대기', '승인', '거절')),
    reason     text,
    created_at timestamptz not null default now(),
    decided_at timestamptz
);

-- 한 학생은 대기 중인 지원을 하나만 가질 수 있음
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';

create table if not exists tf_votes (
    application_id bigint not null references tf_applications(id) on delete cascade,
    voter_id       bigint not null references tf_students(id) on delete cascade,
    vote           text not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)
);

create table if not exists tf_settings (
    key   text primary key,
    value text not null
);

-- AI 호출 기록 (README의 실패 사례 근거)
create table if not exists tf_ai_logs (
    id         bigserial primary key,
    created_at timestamptz not null default now(),
    attempt    int not null,
    model      text not null,
    outcome    text not null,          -- 성공, 시간 초과, 오류, 빈 응답, 검사 실패
    detail     text,
    elapsed_s  numeric(6, 1)
);

-- 2026-11-02 고침: 처음에는 관리자 행 때문에 main_role 에 '관리자'를 7번째 값으로 허용했음.
-- 공고 5절(6개 역할 밖의 값을 막음)에 맞추려고, 이미 만든 DB에 아래 SQL을 실행해 관리자의 main_role 을 비움.
-- 위의 create table 문은 이 변경을 반영한 최종 모양임.
-- alter table tf_students drop constraint tf_students_main_role_check;
-- alter table tf_students alter column main_role drop not null;
-- update tf_students set main_role = null where is_admin;
-- alter table tf_students add constraint tf_students_main_role_check check (main_role in
--     ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자'));
-- alter table tf_students add constraint tf_students_role_required check (is_admin or main_role is not null);
