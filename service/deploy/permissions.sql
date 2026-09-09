-- Run with migration-owner authority after migrate. Explicit psql identifiers:
-- psql -v runtime_role=notron_runtime -v operations_role=notron_ops -f permissions.sql
-- Roles and their secret-manager passwords are provisioned separately.
BEGIN;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"runtime_role", :"operations_role";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"runtime_role", :"operations_role";
GRANT INSERT,UPDATE,DELETE ON accounts,identities,devices,entitlements,webhook_events,
 usage_reservations,worker_leases,audit_metadata,identity_sessions,billing_accounts,
 billing_subscriptions,billing_orders,billing_grants,billing_allocations,usage_meter,
 usage_cache,account_deletions,identity_deletion_tombstones,account_rate_windows
 TO :"runtime_role", :"operations_role";
GRANT INSERT,UPDATE,DELETE ON operator_evidence,cache_key_generation TO :"operations_role";
REVOKE INSERT,UPDATE,DELETE ON operator_evidence,cache_key_generation,schema_migrations FROM :"runtime_role";
REVOKE INSERT,UPDATE,DELETE ON schema_migrations FROM :"operations_role";
COMMIT;
