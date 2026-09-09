SELECT pg_advisory_xact_lock(505002);
DO $$ BEGIN
 IF (SELECT max(version) FROM schema_migrations) <> 5 THEN
  RAISE EXCEPTION 'unsupported migration history';
 END IF;
END $$;
UPDATE schema_migrations SET active=false WHERE version=5;
