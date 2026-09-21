-- AI/project selection is local application state, not release-registry data.
-- Keep the cloud registry limited to hardware identity and lifecycle events.

drop table if exists public.device_assignments;
