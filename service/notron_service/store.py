"""PostgreSQL foundation. Migration is an explicit deployment operation.

The service accepts only server-authenticated Principal objects, never body IDs.
No retry/body content is stored by this foundation. Runtime DB credentials should
have DML access only; migration credentials separately own DDL permissions.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .principals import Principal

MIGRATION = Path(__file__).resolve().parent / 'migrations' / '002_accounts.sql'
if not MIGRATION.is_file():  # Source tree; wheel contains the package resource.
    MIGRATION = Path(__file__).resolve().parents[1] / 'migrations' / '002_accounts.sql'
_TABLES = ('accounts', 'identities', 'devices', 'entitlements', 'webhook_events',
           'usage_reservations', 'worker_leases', 'audit_metadata')


class SchemaMismatch(RuntimeError):
    """Database is not compatible with this service revision."""


class AccessDenied(PermissionError):
    """Managed account/device access is absent or revoked."""


class PostgresStore:
    def __init__(self, database_url: str, *, connect_timeout: int = 3):
        self._database_url = database_url
        self._connect_timeout = connect_timeout

    def _connect(self):
        return psycopg.connect(self._database_url, connect_timeout=self._connect_timeout, row_factory=dict_row)

    @staticmethod
    def _fingerprint(conn) -> str:
        # Includes columns/defaults/nullability, ownership constraints and indexes.
        rows = conn.execute("""
            SELECT c.relname, a.attname, format_type(a.atttypid,a.atttypmod) AS type,
                   a.attnotnull, pg_get_expr(d.adbin,d.adrelid) AS default_expr
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname=current_schema() AND c.relname=ANY(%s)
            ORDER BY c.relname,a.attnum
        """, (list(_TABLES),)).fetchall()
        constraints = conn.execute("""
            SELECT c.relname, k.conname, pg_get_constraintdef(k.oid) AS definition
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relname=ANY(%s)
            ORDER BY c.relname,k.conname
        """, (list(_TABLES),)).fetchall()
        indexes = conn.execute("""
            SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname=current_schema() AND tablename=ANY(%s)
            ORDER BY tablename,indexname
        """, (list(_TABLES),)).fetchall()
        # Ignore schema names so pg_dump/restore into another schema remains valid.
        schema = conn.execute('SELECT current_schema() AS name').fetchone()['name']
        import json
        signature = json.dumps([rows,constraints,indexes], sort_keys=True)
        signature = signature.replace(f'{schema}.', '')
        return sha256(signature.encode()).hexdigest()

    def _check(self, conn, *, allow_inactive=False):
        rows = conn.execute('SELECT version,checksum,fingerprint,active FROM schema_migrations ORDER BY version').fetchall()
        checksum = sha256(MIGRATION.read_bytes()).hexdigest()
        if (len(rows) != 1 or rows[0]['version'] != 2 or rows[0]['checksum'] != checksum
                or (not allow_inactive and not rows[0]['active'])
                or rows[0]['fingerprint'] != self._fingerprint(conn)):
            raise SchemaMismatch('database schema mismatch')

    def migrate(self) -> None:
        with self._connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(505002)')
            conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version integer PRIMARY KEY, checksum text NOT NULL,
                fingerprint text NOT NULL, active boolean NOT NULL DEFAULT true,
                applied_at timestamptz NOT NULL DEFAULT now())""")
            if conn.execute('SELECT 1 FROM schema_migrations').fetchone():
                self._check(conn, allow_inactive=True)
                conn.execute('UPDATE schema_migrations SET active=true WHERE version=2')
            else:
                conn.execute(MIGRATION.read_text())
                conn.execute('INSERT INTO schema_migrations(version,checksum,fingerprint) VALUES (2,%s,%s)',
                             (sha256(MIGRATION.read_bytes()).hexdigest(),self._fingerprint(conn)))
            self._check(conn)

    def readiness(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SET LOCAL statement_timeout='2000ms'")
                self._check(conn)
            return True
        except (psycopg.Error, SchemaMismatch, OSError):
            return False

    @staticmethod
    def _authorize(conn, principal: Principal):
        if not isinstance(principal, Principal) or principal.kind != 'managed' or 'account:read' not in principal.scopes:
            raise AccessDenied('managed account access required')
        row = conn.execute("""
            SELECT a.id,a.status,a.created_at,a.contact_email FROM accounts a
            JOIN devices d ON d.account_id=a.id
            WHERE a.id=%s AND d.id=%s AND d.revoked_at IS NULL AND a.status='active'
        """, (principal.account_id,principal.device_id)).fetchone()
        if row is None:
            raise AccessDenied('managed account access required')
        return row

    def get_account(self, principal: Principal) -> dict:
        with self._connect() as conn:
            return self._authorize(conn, principal)

    def list_devices(self, principal: Principal) -> list[dict]:
        with self._connect() as conn:
            self._authorize(conn, principal)
            return conn.execute('SELECT id,created_at,revoked_at FROM devices WHERE account_id=%s ORDER BY created_at,id',
                                (principal.account_id,)).fetchall()
