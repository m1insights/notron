SELECT pg_advisory_xact_lock(505002);
DO $$ BEGIN
 IF (SELECT max(version) FROM schema_migrations) <> 6 THEN
  RAISE EXCEPTION 'unsupported migration history';
 END IF;
END $$;
-- Non-destructive rollback: retain fences and deletion identities, deny readiness.
UPDATE schema_migrations SET active=false WHERE version=6;
