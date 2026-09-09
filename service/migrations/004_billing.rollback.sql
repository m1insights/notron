-- Stop this revision without erasing payment/grant identities. Restore the matched
-- backup to downgrade; an old binary must not operate on this schema.
DO $$ BEGIN
 IF (SELECT array_agg(version ORDER BY version) FROM schema_migrations) != ARRAY[2,3,4] THEN
  RAISE EXCEPTION 'unsupported migration history';
 END IF;
END $$;
UPDATE schema_migrations SET active=false WHERE version=4;
