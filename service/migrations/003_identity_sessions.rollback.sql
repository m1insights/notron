-- Non-destructive rollback: all identities and revocation tombstones remain.
DO $$ BEGIN
    IF (SELECT array_agg(version ORDER BY version) FROM schema_migrations) <> ARRAY[2,3] THEN
        RAISE EXCEPTION 'unsupported migration history';
    END IF;
END $$;
UPDATE schema_migrations SET active=false WHERE version=3;
