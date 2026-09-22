-- Authenticated firmware lifecycle logging without exposing the database
-- service-role key to the Windows application.

alter table public.devices
  add column if not exists event_token_fingerprint text
  check (
    event_token_fingerprint is null
    or event_token_fingerprint ~ '^[0-9a-f]{64}$'
  );

alter table public.device_events
  drop constraint if exists device_events_event_type_check;

alter table public.device_events
  add constraint device_events_event_type_check
  check (event_type in (
    'provisioned', 'activated', 'heartbeat',
    'firmware_update_started', 'firmware_update_succeeded',
    'firmware_update_failed', 'firmware_rollback',
    'assigned', 'released', 'blocked', 'unblocked', 'rma', 'retired'
  ));

create or replace function public.record_device_event(
  p_hardware_id text,
  p_event_token text,
  p_event_type text,
  p_firmware_version text default null,
  p_metadata jsonb default '{}'::jsonb
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  matched_device public.devices%rowtype;
  inserted_event_id bigint;
begin
  if p_event_token !~ '^[0-9a-fA-F]{64}$' then
    raise exception 'invalid device credentials' using errcode = '28000';
  end if;

  if p_event_type not in (
    'heartbeat', 'firmware_update_started', 'firmware_update_succeeded',
    'firmware_update_failed', 'firmware_rollback'
  ) then
    raise exception 'event type is not accepted from devices' using errcode = '22023';
  end if;

  if octet_length(coalesce(p_metadata, '{}'::jsonb)::text) > 4096 then
    raise exception 'event metadata is too large' using errcode = '22023';
  end if;

  select * into matched_device
  from public.devices
  where hardware_id = upper(p_hardware_id)
    and status not in ('blocked', 'retired')
    and event_token_fingerprint = encode(
      extensions.digest(pg_catalog.decode(lower(p_event_token), 'hex'), 'sha256'),
      'hex'
    );

  if not found then
    raise exception 'invalid device credentials' using errcode = '28000';
  end if;

  insert into public.device_events (
    device_id, event_type, firmware_version, metadata
  ) values (
    matched_device.id,
    p_event_type,
    nullif(p_firmware_version, ''),
    coalesce(p_metadata, '{}'::jsonb)
  )
  returning id into inserted_event_id;

  update public.devices
  set
    firmware_version = case
      when p_event_type = 'firmware_update_succeeded'
        then coalesce(nullif(p_firmware_version, ''), firmware_version)
      else firmware_version
    end,
    last_seen_at = now(),
    updated_at = now()
  where id = matched_device.id;

  return inserted_event_id;
end;
$$;

revoke all on function public.record_device_event(text, text, text, text, jsonb)
  from public;
grant execute on function public.record_device_event(text, text, text, text, jsonb)
  to anon, authenticated;

comment on column public.devices.event_token_fingerprint is
  'SHA-256 fingerprint of a dedicated event token; never reuse the firmware HMAC key.';
