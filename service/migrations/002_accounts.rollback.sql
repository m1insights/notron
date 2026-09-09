-- Non-destructive rollback: disable this schema generation but retain every row,
-- ownership constraint and identity. Stop service/workers first and take a pg_dump
-- backup. This does NOT make old code compatible with newer schemas. Restore the
-- complete backup into a separate database if an actual schema downgrade is needed.
-- Forward: PostgresStore.migrate() verifies checksums/catalog before reactivation.
-- Run transactionally (psql --single-transaction -v ON_ERROR_STOP=1).
SELECT pg_advisory_xact_lock(505002);
DO $$
BEGIN
    IF (SELECT count(*) FROM schema_migrations) <> 1
       OR NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version=2) THEN
        RAISE EXCEPTION 'unsupported migration history';
    END IF;
END $$;
UPDATE schema_migrations SET active=false WHERE version=2;
