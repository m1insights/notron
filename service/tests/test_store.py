"""Real PostgreSQL tests; NOTRON_TEST_DATABASE_URL must point at a disposable socket DB."""
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def db():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    url = os.environ.get('NOTRON_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires explicit disposable PostgreSQL Unix-socket database')
    host = conninfo_to_dict(url).get('host', '')
    if not host.startswith('/'):
        pytest.fail('test database must use a private Unix socket')
    schema = 'test_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
    from psycopg.conninfo import make_conninfo
    scoped = make_conninfo(url, options=f'-c search_path={schema}')
    try:
        yield scoped
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')


def store_for(db):
    from notron_service.store import PostgresStore
    return PostgresStore(db)


def seed(db):
    import psycopg
    accounts = [uuid4(), uuid4()]
    devices = [uuid4(), uuid4()]
    with psycopg.connect(db) as conn:
        for a, d in zip(accounts, devices):
            conn.execute("INSERT INTO accounts(id,status,contact_email) VALUES (%s,'active','same@example.test')", (a,))
            conn.execute("INSERT INTO devices(account_id,id) VALUES (%s,%s)", (a,d))
            conn.execute("INSERT INTO identities(issuer,subject,account_id) VALUES ('https://issuer.test',%s,%s)", (str(a),a))
    from notron_service.principals import Principal
    return [Principal(account_id=a, device_id=d, scopes=frozenset({'account:read'}), kind='managed') for a,d in zip(accounts, devices)]


def test_migration_idempotent_and_readiness_detects_drift(db):
    import psycopg
    from notron_service.store import SchemaMismatch
    store = store_for(db)
    assert not store.readiness()
    store.migrate()
    principals = seed(db)
    store.migrate()
    assert store.readiness()
    assert store.get_account(principals[0])['id'] == principals[0].account_id
    with psycopg.connect(db) as conn:
        conn.execute("UPDATE schema_migrations SET checksum = 'tampered'")
    assert not store.readiness()
    with pytest.raises(SchemaMismatch):
        store.migrate()


def test_readiness_requires_columns_and_constraints(db):
    import psycopg
    store = store_for(db)
    store.migrate()
    with psycopg.connect(db) as conn:
        conn.execute('ALTER TABLE devices DROP COLUMN revoked_at')
    assert not store.readiness()


def test_account_queries_require_owned_active_device(db):
    import psycopg
    from notron_service.store import AccessDenied
    from notron_service.principals import Principal
    store = store_for(db)
    store.migrate()
    a, b = seed(db)
    assert [row['id'] for row in store.list_devices(a)] == [a.device_id]
    forged = Principal(account_id=a.account_id,device_id=b.device_id,scopes=a.scopes,kind='managed')
    with pytest.raises(AccessDenied):
        store.get_account(forged)
    with psycopg.connect(db) as conn:
        conn.execute('UPDATE devices SET revoked_at=now() WHERE account_id=%s', (a.account_id,))
    with pytest.raises(AccessDenied):
        store.list_devices(a)


def test_composite_foreign_keys_reject_cross_account_devices(db):
    import psycopg
    store = store_for(db)
    store.migrate()
    a,b = seed(db)
    with psycopg.connect(db) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute('INSERT INTO worker_leases(account_id,device_id,fence,expires_at) VALUES (%s,%s,1,now())', (a.account_id,b.device_id))


def test_unknown_schema_version_refused(db):
    import psycopg
    from notron_service.store import SchemaMismatch
    store = store_for(db)
    store.migrate()
    with psycopg.connect(db) as conn:
        conn.execute("INSERT INTO schema_migrations(version,checksum,fingerprint) VALUES (999,'future','future')")
    assert not store.readiness()
    with pytest.raises(SchemaMismatch):
        store.migrate()


def test_rollback_preserves_data_and_forward_restores(db):
    import psycopg
    from notron_service.store import SchemaMismatch
    store = store_for(db)
    store.migrate()
    a,b = seed(db)
    rollback = Path(__file__).parents[1] / 'migrations' / '007_operations.rollback.sql'
    with psycopg.connect(db) as conn:
        conn.execute(rollback.read_text())
        assert conn.execute('SELECT count(*) FROM accounts').fetchone()[0] == 2
    assert not store.readiness()
    store.migrate()
    assert store.readiness()
    assert store.get_account(a)['id'] == a.account_id


def test_failed_migration_leaves_no_partial_schema(db, tmp_path, monkeypatch):
    import psycopg
    import notron_service.store as module
    broken = tmp_path / 'broken.sql'
    broken.write_text(module.MIGRATION.read_text() + '\nSELECT missing_column FROM accounts;')
    monkeypatch.setattr(module, 'MIGRATION', broken)
    with pytest.raises(psycopg.errors.UndefinedColumn):
        store_for(db).migrate()
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT to_regclass('accounts')").fetchone()[0] is None
        assert conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0] is None


def test_scope_and_account_suspension_are_enforced(db):
    import psycopg
    from notron_service.principals import Principal
    from notron_service.store import AccessDenied
    store = store_for(db)
    store.migrate()
    a,_ = seed(db)
    unscoped = Principal(account_id=a.account_id, device_id=a.device_id, scopes=frozenset(),kind='managed')
    with pytest.raises(AccessDenied):
        store.get_account(unscoped)
    with pytest.raises(AccessDenied):
        store.get_account(SimpleNamespace(account_id=a.account_id,device_id=a.device_id,scopes=a.scopes,kind='managed'))
    with psycopg.connect(db) as conn:
        conn.execute("UPDATE accounts SET status='suspended' WHERE id=%s", (a.account_id,))
    with pytest.raises(AccessDenied):
        store.get_account(a)


def test_identity_is_issuer_and_subject_not_email(db):
    import psycopg
    store_for(db).migrate()
    a,b = seed(db)
    with psycopg.connect(db) as conn:
        conn.execute("INSERT INTO identities VALUES ('https://another.test',%s,%s)", (str(a.account_id),b.account_id))
    with psycopg.connect(db) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO identities VALUES ('https://issuer.test',%s,%s)", (str(a.account_id),b.account_id))


def test_constraints_are_part_of_readiness(db):
    import psycopg
    store = store_for(db)
    store.migrate()
    with psycopg.connect(db) as conn:
        conn.execute('ALTER TABLE identities DROP CONSTRAINT identities_pkey CASCADE')
    assert not store.readiness()


def test_backup_restore_after_rollback_keeps_identity_and_data(db, tmp_path):
    import subprocess
    import psycopg
    pg_bin = os.environ.get('NOTRON_TEST_PG_BIN')
    if not pg_bin:
        pytest.skip('requires matching pg_dump/pg_restore via NOTRON_TEST_PG_BIN')
    store = store_for(db)
    store.migrate()
    a,b = seed(db)
    with psycopg.connect(db) as conn:
        schema = conn.execute('SELECT current_schema()').fetchone()[0]
    backup = tmp_path / 'foundation.dump'
    subprocess.run([str(Path(pg_bin)/'pg_dump'), '--dbname', db, '--schema', schema,
                    '--format=custom', '--no-owner', '--no-privileges', '--file', str(backup)],
                   check=True, capture_output=True)
    rollback = Path(__file__).parents[1] / 'migrations' / '007_operations.rollback.sql'
    with psycopg.connect(db) as conn:
        conn.execute(rollback.read_text())
    assert not store.readiness()
    # Simulate restoring the matched full backup; only the isolated test schema.
    with psycopg.connect(db) as conn:
        from psycopg import sql
        conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
    subprocess.run([str(Path(pg_bin)/'pg_restore'), '--dbname', db, '--no-owner',
                    '--no-privileges', '--exit-on-error', str(backup)], check=True,capture_output=True)
    assert store.readiness()
    store.migrate()
    assert store.get_account(a)['id'] == a.account_id
    assert store.get_account(b)['id'] == b.account_id


def test_usage_ids_are_account_scoped_and_entitlement_ownership_is_enforced(db):
    import psycopg
    store_for(db).migrate()
    a,b = seed(db)
    entitlement_a, entitlement_b = uuid4(), uuid4()
    with psycopg.connect(db) as conn:
        for principal, entitlement in ((a,entitlement_a),(b,entitlement_b)):
            conn.execute("INSERT INTO entitlements(account_id,id,status) VALUES (%s,%s,'active')",
                         (principal.account_id,entitlement))
            conn.execute('''INSERT INTO usage_reservations
                (account_id,id,device_id,request_id,entitlement_id,payload_digest,reserved_units)
                VALUES (%s,%s,%s,'desktop-operation-1',%s,%s,10)''',
                         (principal.account_id,uuid4(),principal.device_id,entitlement,'a'*64))
    with psycopg.connect(db) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute('''INSERT INTO usage_reservations
                (account_id,id,device_id,request_id,entitlement_id,payload_digest,reserved_units)
                VALUES (%s,%s,%s,'desktop-operation-2',%s,%s,10)''',
                         (a.account_id,uuid4(),a.device_id,entitlement_b,'b'*64))


def test_concurrent_migrations_apply_once(db):
    from concurrent.futures import ThreadPoolExecutor
    store = store_for(db)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(store.migrate) for _ in range(4)]
        for future in futures:
            future.result()
    assert store.readiness()
