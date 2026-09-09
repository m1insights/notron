CREATE TABLE account_rate_windows (
 account_id uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
 started_at timestamptz NOT NULL, requests integer NOT NULL CHECK(requests>0)
);
CREATE TABLE operator_evidence (
 account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 reservation_id uuid NOT NULL,
 evidence_digest text NOT NULL CHECK(evidence_digest ~ '^[0-9a-f]{64}$'),
 actual_micro_usd bigint NOT NULL CHECK(actual_micro_usd>=0),
 recorded_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id,reservation_id)
);
CREATE TABLE cache_key_generation (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 key_digest text NOT NULL CHECK(key_digest ~ '^[0-9a-f]{64}$')
);
