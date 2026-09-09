-- Immutable source identities and integer grant accounting. All writers lock accounts first.
CREATE TABLE billing_accounts (
 account_id uuid PRIMARY KEY REFERENCES accounts(id),
 customer_id text UNIQUE,
 last_attempt_at timestamptz,
 reconciled_at timestamptz
);
CREATE TABLE billing_subscriptions (
 account_id uuid NOT NULL REFERENCES accounts(id),
 subscription_id text PRIMARY KEY,
 status text NOT NULL,
 trial_until timestamptz,
 paid_until timestamptz,
 UNIQUE(account_id,subscription_id)
);
CREATE TABLE billing_orders (
 account_id uuid NOT NULL REFERENCES accounts(id),
 id uuid NOT NULL,
 price_id text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('subscription','topup')),
 units bigint NOT NULL CHECK(units>0),
 ttl_seconds bigint CHECK(ttl_seconds>0),
 return_url text NOT NULL,
 session_id text UNIQUE,
 checkout_url text,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,id)
);
CREATE TABLE billing_grants (
 account_id uuid NOT NULL REFERENCES accounts(id),
 entitlement_id uuid NOT NULL,
 source_id text NOT NULL UNIQUE,
 starts_at timestamptz NOT NULL DEFAULT now(),
 kind text NOT NULL CHECK(kind IN ('subscription','trial','topup','pilot')),
 subscription_id text,
 revoked_at timestamptz,
 PRIMARY KEY(account_id,entitlement_id),
 FOREIGN KEY(account_id,entitlement_id) REFERENCES entitlements(account_id,id),
 FOREIGN KEY(account_id,subscription_id) REFERENCES billing_subscriptions(account_id,subscription_id)
);
CREATE TABLE billing_allocations (
 account_id uuid NOT NULL,
 reservation_id uuid NOT NULL,
 entitlement_id uuid NOT NULL,
 reserved_units bigint NOT NULL CHECK(reserved_units>=0),
 charged_units bigint CHECK(charged_units>=0 AND charged_units<=reserved_units),
 PRIMARY KEY(account_id,reservation_id,entitlement_id),
 FOREIGN KEY(account_id,reservation_id) REFERENCES usage_reservations(account_id,id),
 FOREIGN KEY(account_id,entitlement_id) REFERENCES billing_grants(account_id,entitlement_id)
);
ALTER TABLE webhook_events ADD COLUMN event_type text;
ALTER TABLE webhook_events ADD COLUMN object_id text;
ALTER TABLE webhook_events ADD COLUMN customer_id text;
ALTER TABLE webhook_events ADD COLUMN last_attempt_at timestamptz;
ALTER TABLE webhook_events ADD COLUMN attempts integer NOT NULL DEFAULT 0;
CREATE INDEX billing_grants_account_idx ON billing_grants(account_id,kind);
CREATE INDEX billing_allocations_grant_idx ON billing_allocations(account_id,entitlement_id);
