-- Almacén de tokens de Mercado Libre y bitácora de escrituras.
-- Seguridad: RLS activo SIN políticas + sin permisos para anon/authenticated.
-- Solo el servidor (service key) puede leer o escribir estas tablas.

create table if not exists public.ml_tokens (
  id               int primary key default 1 check (id = 1),  -- una sola cuenta
  access_token     text,
  refresh_token    text,
  expires_at       timestamptz,
  user_id          bigint,
  scope            text,
  lock_until       timestamptz,   -- arrendamiento para renovar el refresh token una sola vez
  pending_state    text,          -- autorización OAuth en curso
  pending_verifier text,
  pending_at       timestamptz,
  updated_at       timestamptz not null default now()
);

create table if not exists public.ml_actions (
  id         bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  tool       text not null,
  request    jsonb,
  result     jsonb
);

alter table public.ml_tokens  enable row level security;
alter table public.ml_actions enable row level security;

revoke all on public.ml_tokens  from anon, authenticated;
revoke all on public.ml_actions from anon, authenticated;
revoke all on sequence public.ml_actions_id_seq from anon, authenticated;

insert into public.ml_tokens (id) values (1) on conflict (id) do nothing;
