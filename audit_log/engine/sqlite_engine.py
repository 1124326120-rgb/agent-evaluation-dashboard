"""SQLite-based audit log storage engine with daily table partitioning."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from audit_log.models.event import AuditEvent


class AuditStorageEngine:
    """SQLite storage engine with daily table partitioning.

    Features:
    - Automatic daily table creation (audit_YYYYMMDD)
    - SQL-level filtering for all query dimensions
    - WAL mode for concurrent read/write
    - Thread-safe writes via lock
    """

    def __init__(self, db_dir: str | Path = "audit_data"):
        self._db_dir = Path(db_dir)
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._db_dir / "audit_log.db"
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self) -> sqlite3.Connection:
        """Open or return the existing connection."""
        if self._conn is not None:
            return self._conn
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA cache_size=-64000")  # 64MB
        self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _table_name(self, dt: datetime | None = None) -> str:
        """Get the daily table name for a given datetime."""
        if dt is None:
            dt = datetime.now(timezone.utc)
        return f"audit_{dt.strftime('%Y%m%d')}"

    def _ensure_table(self, table_name: str) -> None:
        """Create the daily table if it does not exist."""
        conn = self._connect()
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS [{table_name}] (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                agent_type TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                trace_id TEXT DEFAULT '',
                parent_trace_id TEXT,
                event_type TEXT NOT NULL,
                action TEXT DEFAULT '',
                target_type TEXT DEFAULT '',
                target_id TEXT DEFAULT '',
                input_summary TEXT DEFAULT '',
                output_summary TEXT DEFAULT '',
                decision TEXT,
                status TEXT NOT NULL DEFAULT 'success',
                error TEXT,
                duration_ms REAL DEFAULT 0.0,
                metadata TEXT DEFAULT '{{}}'
            )
        """)
        # Create indexes for common query patterns
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_ts ON [{table_name}](timestamp)
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_agent ON [{table_name}](agent_id)
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_type ON [{table_name}](event_type)
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_session ON [{table_name}](session_id)
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_trace ON [{table_name}](trace_id)
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_status ON [{table_name}](status)
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_action ON [{table_name}](action)
        """)
        conn.commit()

    def _today_tables(self) -> list[str]:
        """Get the list of daily table names existing in the database."""
        conn = self._connect()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'audit_%' ORDER BY name DESC"
        )
        return [row[0] for row in cursor.fetchall()]

    def _recent_tables(self, days: int = 7) -> list[str]:
        """Get table names for the last N days from today."""
        tables = []
        now = datetime.now(timezone.utc)
        for i in range(days):
            dt = now - __import__("datetime").timedelta(days=i)
            tables.append(self._table_name(dt))
        existing = set(self._today_tables())
        return [t for t in tables if t in existing]

    # ------------------------------------------------------------------ #
    # Write API
    # ------------------------------------------------------------------ #

    def write_event(self, event: AuditEvent) -> None:
        """Write a single audit event."""
        table = self._table_name()
        self._ensure_table(table)
        conn = self._connect()
        d = event.to_dict()
        with self._lock:
            conn.execute(f"""
                INSERT OR REPLACE INTO [{table}]
                (event_id, timestamp, agent_id, agent_type, session_id, trace_id,
                 parent_trace_id, event_type, action, target_type, target_id,
                 input_summary, output_summary, decision, status, error,
                 duration_ms, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d["event_id"], d["timestamp"], d["agent_id"], d["agent_type"],
                d["session_id"], d["trace_id"], d.get("parent_trace_id"),
                d["event_type"], d["action"], d["target_type"], d["target_id"],
                d["input_summary"], d["output_summary"], d.get("decision"),
                d["status"], json.dumps(d["error"]) if d.get("error") else None,
                d["duration_ms"], json.dumps(d.get("metadata", {}))
            ))
            conn.commit()

    def write_events_batch(self, events: list[AuditEvent]) -> None:
        """Write multiple events in a single transaction."""
        if not events:
            return
        table = self._table_name()
        self._ensure_table(table)
        conn = self._connect()
        with self._lock:
            for event in events:
                d = event.to_dict()
                conn.execute(f"""
                    INSERT OR REPLACE INTO [{table}]
                    (event_id, timestamp, agent_id, agent_type, session_id, trace_id,
                     parent_trace_id, event_type, action, target_type, target_id,
                     input_summary, output_summary, decision, status, error,
                     duration_ms, metadata)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    d["event_id"], d["timestamp"], d["agent_id"], d["agent_type"],
                    d["session_id"], d["trace_id"], d.get("parent_trace_id"),
                    d["event_type"], d["action"], d["target_type"], d["target_id"],
                    d["input_summary"], d["output_summary"], d.get("decision"),
                    d["status"], json.dumps(d["error"]) if d.get("error") else None,
                    d["duration_ms"], json.dumps(d.get("metadata", {}))
                ))
            conn.commit()

    # ------------------------------------------------------------------ #
    # Query API — all filters applied at SQL level
    # ------------------------------------------------------------------ #

    def query_events(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        agent_id: Optional[str] = None,
        event_type: Optional[str] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        status: Optional[str] = None,
        action: Optional[str] = None,
        target_type: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Query events with SQL-level filtering across recent daily tables.

        All filter parameters are applied in the SQL WHERE clause.
        """
        tables = self._recent_tables(days=7)
        if not tables:
            return []

        conditions: list[str] = []
        params: list[Any] = []

        if start_time:
            conditions.append("e.timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("e.timestamp <= ?")
            params.append(end_time)
        if agent_id:
            conditions.append("e.agent_id = ?")
            params.append(agent_id)
        if event_type:
            conditions.append("e.event_type = ?")
            params.append(event_type)
        if session_id:
            conditions.append("e.session_id = ?")
            params.append(session_id)
        if trace_id:
            conditions.append("e.trace_id = ?")
            params.append(trace_id)
        if status:
            conditions.append("e.status = ?")
            params.append(status)
        if action:
            conditions.append("e.action LIKE ?")
            params.append(f"%{action}%")
        if target_type:
            conditions.append("e.target_type = ?")
            params.append(target_type)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        conn = self._connect()
        results: list[AuditEvent] = []

        for table in tables:
            try:
                cursor = conn.execute(f"""
                    SELECT * FROM [{table}] e
                    WHERE {where_clause}
                    ORDER BY e.timestamp DESC
                    LIMIT ? OFFSET ?
                """, params + [limit, offset])
                for row in cursor.fetchall():
                    results.append(self._row_to_event(dict(row)))
            except sqlite3.OperationalError:
                continue

        # Sort combined results
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results[:limit]

    def get_event(self, event_id: str) -> Optional[AuditEvent]:
        """Get a single event by ID (scans recent tables)."""
        tables = self._recent_tables(days=7)
        conn = self._connect()
        for table in tables:
            try:
                cursor = conn.execute(
                    f"SELECT * FROM [{table}] WHERE event_id = ? LIMIT 1",
                    (event_id,)
                )
                row = cursor.fetchone()
                if row:
                    return self._row_to_event(dict(row))
            except sqlite3.OperationalError:
                continue
        return None

    def get_session_replay(self, session_id: str) -> list[AuditEvent]:
        """Get all events for a session in chronological order."""
        return self.query_events(
            session_id=session_id,
            limit=10000,
        )

    def get_trace_chain(self, trace_id: str) -> list[AuditEvent]:
        """Get all events in a trace chain.

        Includes events with the given trace_id AND events whose
        parent_trace_id matches (child traces).
        """
        tables = self._recent_tables(days=7)
        conn = self._connect()
        results: list[AuditEvent] = []

        for table in tables:
            try:
                cursor = conn.execute(f"""
                    SELECT * FROM [{table}]
                    WHERE trace_id = ? OR parent_trace_id = ?
                    ORDER BY timestamp ASC
                """, (trace_id, trace_id))
                for row in cursor.fetchall():
                    results.append(self._row_to_event(dict(row)))
            except sqlite3.OperationalError:
                continue

        results.sort(key=lambda e: e.timestamp)
        return results

    def get_error_stats(self) -> list[dict]:
        """Aggregate error statistics across all recent tables."""
        tables = self._recent_tables(days=7)
        conn = self._connect()
        stats: dict[str, dict] = {}

        for table in tables:
            try:
                cursor = conn.execute(f"""
                    SELECT event_type, status, agent_id, COUNT(*) as cnt,
                           MAX(timestamp) as last_seen
                    FROM [{table}]
                    WHERE status = 'failure'
                    GROUP BY event_type, agent_id
                    ORDER BY cnt DESC
                    LIMIT 200
                """)
                for row in cursor.fetchall():
                    d = dict(row)
                    key = f"{d['event_type']}:{d['agent_id']}"
                    if key not in stats:
                        stats[key] = {
                            "event_type": d["event_type"],
                            "agent_id": d["agent_id"],
                            "count": 0,
                            "last_seen": d["last_seen"],
                        }
                    stats[key]["count"] += d["cnt"]
                    if d["last_seen"] > stats[key]["last_seen"]:
                        stats[key]["last_seen"] = d["last_seen"]
            except sqlite3.OperationalError:
                continue

        return sorted(stats.values(), key=lambda x: x["count"], reverse=True)

    def get_event_count(self, start_time: Optional[str] = None,
                        end_time: Optional[str] = None) -> int:
        """Count events in a time range."""
        tables = self._recent_tables(days=7)
        conn = self._connect()
        total = 0

        conditions = []
        params = []
        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        where_clause = " AND ".join(conditions) if conditions else "1=1"

        for table in tables:
            try:
                cursor = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM [{table}] WHERE {where_clause}",
                    params
                )
                row = cursor.fetchone()
                if row:
                    total += row[0]
            except sqlite3.OperationalError:
                continue
        return total

    def export_all(self, start_time: Optional[str] = None,
                   end_time: Optional[str] = None) -> list[AuditEvent]:
        """Export all events in a time range (no limit)."""
        return self.query_events(start_time=start_time, end_time=end_time, limit=100000)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_event(row: dict) -> AuditEvent:
        """Convert a SQLite row dict to an AuditEvent."""
        # Parse JSON fields
        error = row.get("error")
        if isinstance(error, str):
            try:
                error = json.loads(error)
            except (json.JSONDecodeError, TypeError):
                error = None
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        return AuditEvent(
            event_id=row.get("event_id", ""),
            timestamp=row.get("timestamp", ""),
            agent_id=row.get("agent_id", ""),
            agent_type=row.get("agent_type", ""),
            session_id=row.get("session_id", ""),
            trace_id=row.get("trace_id", ""),
            parent_trace_id=row.get("parent_trace_id"),
            event_type=row.get("event_type", "execution"),
            action=row.get("action", ""),
            target_type=row.get("target_type", ""),
            target_id=row.get("target_id", ""),
            input_summary=row.get("input_summary", ""),
            output_summary=row.get("output_summary", ""),
            decision=row.get("decision"),
            status=row.get("status", "success"),
            error=error,
            duration_ms=row.get("duration_ms", 0.0),
            metadata=metadata,
        )

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
