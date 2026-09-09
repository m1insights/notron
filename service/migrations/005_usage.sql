CREATE TABLE usage_meter (
 account_id uuid NOT NULL,
 reservation_id uuid NOT NULL,
 month date NOT NULL,
 rate_version text NOT NULL,
 reserved_micro_usd bigint NOT NULL CHECK(reserved_micro_usd>0),
 actual_micro_usd bigint CHECK(actual_micro_usd>=0),
 PRIMARY KEY(account_id,reservation_id),
 FOREIGN KEY(account_id,reservation_id) REFERENCES usage_reservations(account_id,id)
);
CREATE INDEX usage_meter_month_idx ON usage_meter(month);
CREATE TABLE usage_cache (
 account_id uuid NOT NULL,
 reservation_id uuid NOT NULL,
 ciphertext bytea NOT NULL,
 expires_at timestamptz NOT NULL,
 PRIMARY KEY(account_id,reservation_id),
 FOREIGN KEY(account_id,reservation_id) REFERENCES usage_reservations(account_id,id)
);
CREATE INDEX usage_cache_expiry_idx ON usage_cache(expires_at);
