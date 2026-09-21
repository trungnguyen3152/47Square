-- AI Status Light central device registry.
-- Provisioning writes use a server-side service role / Edge Function only.

create extension if not exists pgcrypto;

create table if not exists public.devices (
  id uuid primary key default gen_random_uuid(),
  serial_number text not null unique
    check (serial_number ~ '^ASL-[0-9]{4}-[0-9]{6}$'),
  hardware_id text not null unique
    check (hardware_id ~ '^[0-9A-F]{12}$'),
  model text not null default 'XIAO-ESP32C3',
  hardware_revision text not null default 'HW-1.0',
  firmware_version text not null default '1.0.0',
  auth_key_fingerprint text not null
    check (auth_key_fingerprint ~ '^[0-9a-f]{64}$'),
  status text not null default 'inventory'
    check (status in ('inventory', 'active', 'blocked', 'rma', 'retired')),
  owner_id uuid references auth.users(id) on delete set null,
  manufactured_at timestamptz not null default now(),
  activated_at timestamptz,
  last_seen_at timestamptz,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.device_assignments (
  id uuid primary key default gen_random_uuid(),
  device_id uuid not null references public.devices(id) on delete cascade,
  provider text not null
    check (provider in (
      'codex', 'claude-desktop', 'claude-code', 'antigravity',
      'cursor', 'copilot-app', 'copilot-vscode', 'copilot-cli'
    )),
  project_fingerprint text
    check (project_fingerprint is null or project_fingerprint ~ '^[0-9a-f]{64}$'),
  project_label text,
  active boolean not null default true,
  assigned_at timestamptz not null default now(),
  released_at timestamptz
);

create unique index if not exists one_active_assignment_per_device
  on public.device_assignments(device_id) where active;

create table if not exists public.device_events (
  id bigint generated always as identity primary key,
  device_id uuid not null references public.devices(id) on delete cascade,
  event_type text not null
    check (event_type in (
      'provisioned', 'activated', 'heartbeat', 'firmware_updated',
      'assigned', 'released', 'blocked', 'unblocked', 'rma', 'retired'
    )),
  firmware_version text,
  metadata jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now()
);

create index if not exists device_events_device_time
  on public.device_events(device_id, occurred_at desc);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists devices_set_updated_at on public.devices;
create trigger devices_set_updated_at
before update on public.devices
for each row execute function public.set_updated_at();

alter table public.devices enable row level security;
alter table public.device_assignments enable row level security;
alter table public.device_events enable row level security;

-- Signed-out clients cannot access registry data. Signed-in customers can
-- read only devices they own. Creation and mutation remain server-side.
revoke all on public.devices from anon, authenticated;
revoke all on public.device_assignments from anon, authenticated;
revoke all on public.device_events from anon, authenticated;
grant select on public.devices to authenticated;
grant select on public.device_assignments to authenticated;
grant select on public.device_events to authenticated;

drop policy if exists "owners read devices" on public.devices;
create policy "owners read devices"
on public.devices for select to authenticated
using (owner_id = (select auth.uid()));

drop policy if exists "owners read assignments" on public.device_assignments;
create policy "owners read assignments"
on public.device_assignments for select to authenticated
using (
  exists (
    select 1 from public.devices
    where devices.id = device_assignments.device_id
      and devices.owner_id = (select auth.uid())
  )
);

drop policy if exists "owners read events" on public.device_events;
create policy "owners read events"
on public.device_events for select to authenticated
using (
  exists (
    select 1 from public.devices
    where devices.id = device_events.device_id
      and devices.owner_id = (select auth.uid())
  )
);

comment on column public.devices.auth_key_fingerprint is
  'SHA-256 fingerprint only. Never store the raw per-device HMAC key here.';
comment on column public.device_assignments.project_fingerprint is
  'Optional SHA-256 of canonical project identity; avoid uploading local paths.';
