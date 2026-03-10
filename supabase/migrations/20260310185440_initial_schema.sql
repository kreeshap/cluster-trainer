-- ============================================================
--  Cluster Trainer — Initial Schema
--  Run via: supabase db push
-- ============================================================

-- Enable UUID extension (already on by default in Supabase)
create extension if not exists "pgcrypto";


-- ── CLUSTERS ──────────────────────────────────────────────
create table if not exists clusters (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,          -- e.g. "Finance"
  code        text not null unique,          -- e.g. "FI"
  description text,
  created_at  timestamptz default now()
);


-- ── KPIs ──────────────────────────────────────────────────
create table if not exists kpis (
  id                   uuid primary key default gen_random_uuid(),
  kpi_code             text not null unique,   -- e.g. "FI:062"
  kpi_name             text not null,
  cluster_id           uuid references clusters(id) on delete cascade,
  cluster              text not null,          -- denormalized for fast queries
  difficulty           text check (difficulty in ('easy','medium','hard')),
  definition           text,
  formula              text,
  real_world_context   text,
  common_misconceptions text,
  related_kpis         text[],
  easy_angle           text,
  medium_angle         text,
  hard_angle           text,
  created_at           timestamptz default now()
);


-- ── QUESTIONS ─────────────────────────────────────────────
create table if not exists questions (
  id            uuid primary key default gen_random_uuid(),
  scenario      text,
  question      text not null,
  answer_a      text not null,
  answer_b      text not null,
  answer_c      text not null,
  answer_d      text not null,
  correct       text not null check (correct in ('A','B','C','D')),
  explanation   text,
  kpi_code      text not null references kpis(kpi_code) on delete cascade,
  cluster       text not null,
  question_type text check (question_type in ('calculation','scenario','definition','application')),
  difficulty    text check (difficulty in ('easy','medium','hard')),
  source        text default 'generated' check (source in ('generated','parsed')),
  created_at    timestamptz default now()
);

-- Index for fast cluster/kpi filtering
create index if not exists idx_questions_kpi_code  on questions(kpi_code);
create index if not exists idx_questions_cluster   on questions(cluster);
create index if not exists idx_questions_difficulty on questions(difficulty);


-- ── QUIZ HISTORY (User Attempts) ─────────────────────────
create table if not exists quiz_history (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  question_id     uuid not null references questions(id) on delete cascade,
  kpi_code        text not null,
  selected_answer text not null check (selected_answer in ('A','B','C','D')),
  is_correct      boolean not null,
  time_taken      int,    -- seconds
  created_at      timestamptz default now()
);

-- Index for user history lookups
create index if not exists idx_quiz_history_user_id  on quiz_history(user_id);
create index if not exists idx_quiz_history_kpi_code on quiz_history(kpi_code);


-- ── ROW LEVEL SECURITY ────────────────────────────────────
alter table clusters     enable row level security;
alter table kpis         enable row level security;
alter table questions    enable row level security;
alter table quiz_history enable row level security;

-- Public read on clusters and kpis
create policy "Public read clusters"  on clusters  for select using (true);
create policy "Public read kpis"      on kpis      for select using (true);
create policy "Public read questions" on questions for select using (true);

-- Users can only see/write their own quiz history
create policy "Own quiz history select" on quiz_history
  for select using (auth.uid() = user_id);

create policy "Own quiz history insert" on quiz_history
  for insert with check (auth.uid() = user_id);