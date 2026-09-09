-- Additive foundation. Contact email never establishes identity or ownership.
CREATE TABLE accounts (
    id uuid PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('active','suspended','deleting','deleted')),
    contact_email text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE identities (
    issuer text NOT NULL CHECK (length(issuer)>0),
    subject text NOT NULL CHECK (length(subject)>0),
    account_id uuid NOT NULL REFERENCES accounts(id),
    PRIMARY KEY (issuer,subject)
);
CREATE INDEX identities_account_idx ON identities(account_id);
CREATE TABLE devices (
    account_id uuid NOT NULL REFERENCES accounts(id),
    id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    PRIMARY KEY (account_id,id)
);
CREATE TABLE entitlements (
    account_id uuid NOT NULL REFERENCES accounts(id),
    id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('inactive','active','trialing','paused','revoked')),
    access_until timestamptz,
    allowance_units bigint NOT NULL DEFAULT 0 CHECK (allowance_units >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id,id)
);
CREATE TABLE webhook_events (
    provider text NOT NULL CHECK (provider IN ('stripe')),
    event_id text NOT NULL,
    account_id uuid REFERENCES accounts(id),
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    status text NOT NULL DEFAULT 'received' CHECK (status IN ('received','processing','processed','failed')),
    PRIMARY KEY (provider,event_id),
    UNIQUE (account_id,provider,event_id)
);
CREATE INDEX webhook_events_account_idx ON webhook_events(account_id);
CREATE INDEX webhook_events_pending_idx ON webhook_events(status,received_at);
CREATE TABLE usage_reservations (
    account_id uuid NOT NULL REFERENCES accounts(id),
    id uuid NOT NULL,
    device_id uuid NOT NULL,
    request_id text NOT NULL CHECK (length(request_id) BETWEEN 1 AND 512),
    entitlement_id uuid NOT NULL,
    payload_digest text NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
    reserved_units bigint NOT NULL CHECK (reserved_units >= 0),
    actual_units bigint CHECK (actual_units >= 0),
    status text NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','settled','released','uncertain')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id,id),
    UNIQUE (account_id,request_id),
    FOREIGN KEY (account_id,device_id) REFERENCES devices(account_id,id),
    FOREIGN KEY (account_id,entitlement_id) REFERENCES entitlements(account_id,id)
);
CREATE INDEX usage_reservations_device_idx ON usage_reservations(account_id,device_id);
CREATE INDEX usage_reservations_entitlement_idx ON usage_reservations(account_id,entitlement_id,status);
CREATE TABLE worker_leases (
    account_id uuid PRIMARY KEY REFERENCES accounts(id),
    device_id uuid NOT NULL,
    fence bigint NOT NULL CHECK (fence > 0),
    expires_at timestamptz NOT NULL,
    FOREIGN KEY (account_id,device_id) REFERENCES devices(account_id,id)
);
CREATE INDEX worker_leases_device_idx ON worker_leases(account_id,device_id);
CREATE TABLE audit_metadata (
    account_id uuid NOT NULL REFERENCES accounts(id),
    id uuid NOT NULL,
    device_id uuid,
    request_id text CHECK (length(request_id) BETWEEN 1 AND 512),
    event_code text NOT NULL CHECK (event_code ~ '^[a-z_]{1,64}$'),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
    PRIMARY KEY (account_id,id),
    FOREIGN KEY (account_id,device_id) REFERENCES devices(account_id,id)
);
CREATE INDEX audit_metadata_device_idx ON audit_metadata(account_id,device_id);
CREATE INDEX audit_metadata_expiry_idx ON audit_metadata(expires_at);
