-- Append-only upgrade. Retain the previous expiry even after releasing a grant.
ALTER TABLE worker_leases ADD COLUMN not_before timestamptz NOT NULL DEFAULT '1970-01-01 00:00:00+00';
ALTER TABLE worker_leases ADD COLUMN released_at timestamptz;
CREATE TABLE account_deletions (
 account_id uuid PRIMARY KEY REFERENCES accounts(id),
 requested_at timestamptz NOT NULL DEFAULT now(),
 financial_delete_after timestamptz,
 security_delete_after timestamptz NOT NULL DEFAULT (now()+interval '30 days')
);
CREATE TABLE identity_deletion_tombstones (
 digest text PRIMARY KEY CHECK(digest ~ '^[0-9a-f]{64}$')
);
-- Durable deletion outbox. Remote completion is distinct from local access erasure.
ALTER TABLE account_deletions ADD COLUMN billing_cleanup_pending boolean NOT NULL DEFAULT true;
ALTER TABLE account_deletions ADD COLUMN identity_cleanup_pending boolean NOT NULL DEFAULT true;
ALTER TABLE account_deletions ADD COLUMN remote_attempt_at timestamptz;
ALTER TABLE account_deletions ADD COLUMN remote_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE account_deletions ADD COLUMN identity_confirmation_digest text;
