SELECT pg_advisory_xact_lock(505002);
DO $$ BEGIN
 IF (SELECT max(version) FROM schema_migrations) <> 7 THEN
  RAISE EXCEPTION 'unsupported migration history';
 END IF;
END $$;
-- Retain abuse, accounting and key generation state; deny readiness.
UPDATE schema_migrations SET active=false WHERE version=7;
