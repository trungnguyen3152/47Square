# Supabase Device Registry

Run the migration in `migrations/202609220001_device_registry.sql` once in the
Supabase SQL Editor. It creates:

- `devices`: factory identity, firmware, lifecycle, owner and key fingerprint.
- `device_events`: append-only lifecycle and firmware audit history.

Then run `migrations/202609220002_remove_device_assignments.sql`. AI and project
selection stays only in the local desktop configuration; it is intentionally
excluded from the central release registry.

`migrations/202609220003_firmware_update_events.sql` adds a dedicated per-device
event-token fingerprint and the narrow `record_device_event` RPC. The desktop
application can record heartbeat and firmware outcomes with the publishable key,
while the database service-role key remains server-side.

RLS is enabled on every table. Anonymous access is revoked. Authenticated users
can only read rows belonging to their own `auth.uid()`. Provisioning and all
mutations must go through a server-side Edge Function using the service role;
never bundle that role key in the desktop application.

The raw ESP32 authentication key is intentionally absent. Store only its
SHA-256 fingerprint in `devices.auth_key_fingerprint`. If server-side recovery
of raw keys is ever required, keep them in a dedicated secret manager rather
than a public-schema table.
