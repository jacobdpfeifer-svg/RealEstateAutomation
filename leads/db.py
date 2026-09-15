from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Iterable, Optional, Union

DEFAULT_DB = Path(__file__).resolve().parent.parent / "leads.db"
SCHEMA_VERSION = 1

# Backing store is chosen at connect() time via DATABASE_URL. Unset (or
# sqlite:// / a bare path) keeps the original local-file SQLite behavior;
# postgres:// or postgresql:// switches to Postgres via PGConnection below.
# See docs/PROJECT_PLAN.md §8 for why this was held back until git existed.
_PG_SCHEMES = ("postgres://", "postgresql://")


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def _is_postgres_url(url: str) -> bool:
    return url.startswith(_PG_SCHEMES)


_NAMED_PARAM_RE = re.compile(r":(\w+)")


def _to_pg_sql(sql: str) -> str:
    """Translate sqlite-style `?` / `:name` placeholders to psycopg2's `%s` / `%(name)s`."""
    sql = _NAMED_PARAM_RE.sub(r"%(\1)s", sql)
    return sql.replace("?", "%s")


class PGConnection:
    """Thin wrapper giving a psycopg2 connection the sqlite3.Connection interface
    the rest of this codebase already uses: .execute()/.executescript()/.commit()/
    .rollback()/.close(), with rows addressable by column name."""

    def __init__(self, dsn: str) -> None:
        import psycopg2
        import psycopg2.extras

        self._psycopg2 = psycopg2
        self._cursor_factory = psycopg2.extras.RealDictCursor
        self._conn = psycopg2.connect(dsn)

    def execute(self, sql: str, params: Any = ()):
        cur = self._conn.cursor(cursor_factory=self._cursor_factory)
        cur.execute(_to_pg_sql(sql), params)
        return cur

    def executescript(self, sql: str) -> None:
        cur = self._conn.cursor()
        cur.execute(sql)
        cur.close()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


Connection = Union[sqlite3.Connection, PGConnection]

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS case_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    county_fips TEXT NOT NULL,
    case_number TEXT NOT NULL,
    plaintiff TEXT NOT NULL,
    defendant_raw TEXT NOT NULL,
    defendant_normalized TEXT NOT NULL,
    case_type TEXT NOT NULL DEFAULT '',
    filed_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    retrieved_at TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS property_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES case_record(id),
    apn TEXT NOT NULL DEFAULT '',
    situs_address TEXT NOT NULL DEFAULT '',
    owner_of_record TEXT NOT NULL DEFAULT '',
    tax_delinquent_amt REAL,
    years_delinquent INTEGER,
    assessor_url TEXT NOT NULL DEFAULT '',
    match_confidence REAL NOT NULL DEFAULT 0,
    property_type TEXT NOT NULL DEFAULT '',
    total_value REAL,
    dedupe_key TEXT NOT NULL,
    UNIQUE(case_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS contact_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL REFERENCES property_record(id),
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    retrieved_at TEXT NOT NULL,
    manual_paste INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lead (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL UNIQUE REFERENCES case_record(id),
    property_id INTEGER REFERENCES property_record(id),
    contact_id INTEGER REFERENCES contact_record(id),
    pipeline_status TEXT NOT NULL DEFAULT 'new',
    priority_score REAL NOT NULL DEFAULT 0,
    review_note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    url TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    artifact_path TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    retrieved_at TEXT NOT NULL
);

-- Phone/manual contact log (distinct from the Gmail-draft outreach subsystem in Phase 5,
-- which owns the "outreach" name for its own drafted/queued/sent email audit trail).
CREATE TABLE IF NOT EXISTS call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES lead(id),
    channel TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',
    offer_amount REAL,
    notes TEXT NOT NULL DEFAULT '',
    called_at TEXT NOT NULL
);

-- Append-only outcomes ledger: one row per meaningful event tied to any
-- entity (lead, buyer match, drafted/sent email, etc). Rows are never
-- updated or deleted, only inserted — this is the record the matching and
-- outreach subsystems learn from later (matching / outreach phases).
CREATE TABLE IF NOT EXISTS outcome_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0,
    channel TEXT NOT NULL DEFAULT '',
    template_version TEXT NOT NULL DEFAULT '',
    matching_version TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_case_county ON case_record(county_fips);
CREATE INDEX IF NOT EXISTS idx_lead_status ON lead(pipeline_status);
CREATE INDEX IF NOT EXISTS idx_property_case ON property_record(case_id);
CREATE INDEX IF NOT EXISTS idx_outcome_entity ON outcome_event(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_outcome_event_type ON outcome_event(event_type);
CREATE INDEX IF NOT EXISTS idx_outcome_template ON outcome_event(template_version);
"""

# Same tables for Postgres: SERIAL replaces INTEGER PRIMARY KEY AUTOINCREMENT;
# everything else (types, ON CONFLICT/EXCLUDED, indexes) is valid on both engines.
SCHEMA_POSTGRES = re.sub(
    r"INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY", SCHEMA_SQLITE
)

_PG_EXTRA_COLUMNS = (
    ("property_record", "property_type", "TEXT NOT NULL DEFAULT ''"),
    ("property_record", "total_value", "REAL"),
    ("lead", "enrichment_attempts", "INTEGER NOT NULL DEFAULT 0"),
)


def connect(db_path: Path | str | None = None) -> Connection:
    dsn = _database_url()
    if _is_postgres_url(dsn):
        return PGConnection(dsn)
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: Connection) -> None:
    if isinstance(conn, PGConnection):
        # Postgres DDL (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS) is already
        # idempotent, so it doesn't need the sqlite user_version gate below.
        conn.executescript(SCHEMA_POSTGRES)
        for table, column, ddl in _PG_EXTRA_COLUMNS:
            _ensure_column_pg(conn, table, column, ddl)
        conn.commit()
        return

    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError("Database schema is newer than this application")
    if version == 0:
        try:
            conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQLITE)
            _ensure_column(conn, "property_record", "property_type", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "property_record", "total_value", "REAL")
            _ensure_column(conn, "lead", "enrichment_attempts", "INTEGER NOT NULL DEFAULT 0")
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    # Identifiers/DDL are internal constants, never data from records or CLI input.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _ensure_column_pg(conn: PGConnection, table: str, column: str, ddl: str) -> None:
    conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")


@contextmanager
def db_session(db_path: Path | str | None = None) -> Generator[Connection, None, None]:
    conn = connect(db_path)
    try:
        migrate(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log_fetch(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: Optional[int],
    url: str,
    http_status: int,
    artifact_path: str = "",
    error: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log (entity_type, entity_id, url, http_status, artifact_path, error, retrieved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (entity_type, entity_id, url, http_status, artifact_path, error, datetime.utcnow().isoformat()),
    )


def upsert_case(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    conn.execute(
        """
        INSERT INTO case_record (
            county_fips, case_number, plaintiff, defendant_raw, defendant_normalized,
            case_type, filed_date, status, source_url, retrieved_at, dedupe_key
        ) VALUES (
            :county_fips, :case_number, :plaintiff, :defendant_raw, :defendant_normalized,
            :case_type, :filed_date, :status, :source_url, :retrieved_at, :dedupe_key
        )
        ON CONFLICT(dedupe_key) DO UPDATE SET
            status = excluded.status,
            retrieved_at = excluded.retrieved_at
        """,
        row,
    )
    cur = conn.execute("SELECT id FROM case_record WHERE dedupe_key = ?", (row["dedupe_key"],))
    return int(cur.fetchone()["id"])


def ensure_lead(conn: sqlite3.Connection, case_id: int, status: str = "new") -> int:
    now = datetime.utcnow().isoformat()
    conn.execute(
        """
        INSERT INTO lead (case_id, pipeline_status, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(case_id) DO NOTHING
        """,
        (case_id, status, now),
    )
    cur = conn.execute("SELECT id FROM lead WHERE case_id = ?", (case_id,))
    return int(cur.fetchone()["id"])


def update_lead_status(conn: sqlite3.Connection, lead_id: int, status: str, note: str = "") -> None:
    conn.execute(
        """
        UPDATE lead SET pipeline_status = ?, review_note = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, note, datetime.utcnow().isoformat(), lead_id),
    )


def state_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.county_fips,
               COUNT(DISTINCT c.id) AS cases,
               SUM(CASE WHEN l.pipeline_status = 'needs_review' THEN 1 ELSE 0 END) AS review_backlog,
               SUM(CASE WHEN l.pipeline_status = 'error' THEN 1 ELSE 0 END) AS errors,
               MAX(c.retrieved_at) AS last_ingest
        FROM case_record c
        LEFT JOIN lead l ON l.case_id = c.id
        GROUP BY c.county_fips
        ORDER BY c.county_fips
        """
    ).fetchall()
    return [dict(r) for r in rows]


def list_leads(conn: sqlite3.Connection, status: str | None = None) -> Iterable[sqlite3.Row]:
    if status:
        return conn.execute(
            """
            SELECT l.*, c.case_number, c.defendant_raw, c.plaintiff, c.filed_date,
                   p.situs_address, p.apn, p.match_confidence, p.property_type, p.total_value,
                   ct.phone, ct.email, ct.name AS contact_name
            FROM lead l
            JOIN case_record c ON c.id = l.case_id
            LEFT JOIN property_record p ON p.id = l.property_id
            LEFT JOIN contact_record ct ON ct.id = l.contact_id
            WHERE l.pipeline_status = ?
            ORDER BY l.priority_score DESC, c.filed_date DESC
            """,
            (status,),
        ).fetchall()
    return conn.execute(
        """
            SELECT l.*, c.case_number, c.defendant_raw, c.plaintiff, c.filed_date,
                   p.situs_address, p.apn, p.match_confidence, p.property_type, p.total_value,
                   ct.phone, ct.email, ct.name AS contact_name
            FROM lead l
            JOIN case_record c ON c.id = l.case_id
            LEFT JOIN property_record p ON p.id = l.property_id
            LEFT JOIN contact_record ct ON ct.id = l.contact_id
            ORDER BY l.priority_score DESC, c.filed_date DESC
        """
    ).fetchall()


def insert_outcome_event(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int,
    event_type: str,
    weight: float,
    channel: str = "",
    template_version: str = "",
    matching_version: str = "",
    notes: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO outcome_event (
            entity_type, entity_id, event_type, weight, channel,
            template_version, matching_version, notes, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            entity_type,
            entity_id,
            event_type,
            weight,
            channel,
            template_version,
            matching_version,
            notes,
            datetime.utcnow().isoformat(),
        ),
    )
    return int(cur.fetchone()["id"])


def outcome_events(
    conn: sqlite3.Connection,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM outcome_event"
    clauses = []
    params: list[Any] = []
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id"
    return conn.execute(query, params).fetchall()


def outcome_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Aggregate the ledger by (entity_type, event_type, template_version, matching_version)."""
    rows = conn.execute(
        """
        SELECT entity_type, event_type, template_version, matching_version,
               COUNT(*) AS n,
               SUM(weight) AS total_weight,
               AVG(weight) AS avg_weight
        FROM outcome_event
        GROUP BY entity_type, event_type, template_version, matching_version
        ORDER BY total_weight DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]
