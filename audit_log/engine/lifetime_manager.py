"""Lifecycle management for audit logs: rotation, compression, cleanup."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


class DailySummary:
    """Aggregated summary for a single day."""

    def __init__(self, date_str: str, total_events: int = 0,
                 by_type: Optional[dict] = None,
                 by_status: Optional[dict] = None,
                 by_agent: Optional[dict] = None):
        self.date_str = date_str
        self.total_events = total_events
        self.by_type = by_type or {}
        self.by_status = by_status or {}
        self.by_agent = by_agent or {}

    def to_dict(self) -> dict:
        return {
            "date": self.date_str,
            "total_events": self.total_events,
            "by_type": self.by_type,
            "by_status": self.by_status,
            "by_agent": self.by_agent,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DailySummary:
        return cls(
            date_str=data["date"],
            total_events=data.get("total_events", 0),
            by_type=data.get("by_type", {}),
            by_status=data.get("by_status", {}),
            by_agent=data.get("by_agent", {}),
        )


class LifetimeManager:
    """Manages audit log lifecycle: hot → warm → cold.

    - Hot (0-7 days): full SQLite tables
    - Warm (7-30 days): gzip-compressed JSON files
    - Cold (30+ days): aggregated summaries only, raw data archived/deleted

    Runs a background thread to check periodically.
    """

    def __init__(self, db_dir: str | Path = "audit_data",
                 check_interval_seconds: int = 3600,
                 hot_days: int = 7,
                 warm_days: int = 30):
        self._db_dir = Path(db_dir)
        self._warm_dir = self._db_dir / "warm"
        self._cold_dir = self._db_dir / "cold"
        self._warm_dir.mkdir(parents=True, exist_ok=True)
        self._cold_dir.mkdir(parents=True, exist_ok=True)
        self._hot_days = hot_days
        self._warm_days = warm_days
        self._check_interval = check_interval_seconds
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background lifecycle management thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background thread."""
        self._running = False

    def _run_loop(self) -> None:
        """Background loop that checks and applies lifecycle policies."""
        while self._running:
            try:
                self.rotate()
            except Exception:
                pass  # Don't crash the daemon thread
            time.sleep(self._check_interval)

    def rotate(self) -> None:
        """Apply rotation policies: move old data to warm, aggregate cold."""
        now = datetime.now(timezone.utc)
        cutoff_hot = now - timedelta(days=self._hot_days)
        cutoff_warm = now - timedelta(days=self._warm_days)

        # Find daily SQLite tables past hot cutoff
        db_path = self._db_dir / "audit_log.db"
        if not db_path.exists():
            return

        # For each table past hot cutoff, compress to warm storage
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'audit_%'"
        )
        for row in cursor.fetchall():
            table_name = row[0]
            date_str = table_name.replace("audit_", "")
            try:
                table_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            warm_target = self._warm_dir / f"{table_name}.json.gz"
            cold_target = self._cold_dir / f"{table_name}_summary.json"

            if table_date < cutoff_warm:
                # Cold: store aggregated summary only
                self._write_cold_summary(conn, table_name, cold_target)
                self._drop_table(conn, table_name)
            elif table_date < cutoff_hot and not warm_target.exists():
                # Warm: compress and archive
                self._write_warm_archive(conn, table_name, warm_target)
                self._drop_table(conn, table_name)

        conn.close()

    def _write_warm_archive(self, conn, table_name: str, target_path: Path) -> None:
        """Export daily table to gzip-compressed JSON."""
        cursor = conn.execute(f"SELECT * FROM [{table_name}]")
        rows = [dict(r) for r in cursor.fetchall()]
        compressed = gzip.compress(json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8"))
        with open(target_path, "wb") as f:
            f.write(compressed)

    def _write_cold_summary(self, conn, table_name: str, target_path: Path) -> None:
        """Aggregate daily table into a summary and save."""
        cursor = conn.execute(f"""
            SELECT event_type, status, agent_id, COUNT(*) as cnt
            FROM [{table_name}]
            GROUP BY event_type, status, agent_id
        """)
        by_type = {}
        by_status = {}
        by_agent = {}
        total = 0
        for row in cursor.fetchall():
            r = dict(row)
            total += r["cnt"]
            by_type[r["event_type"]] = by_type.get(r["event_type"], 0) + r["cnt"]
            by_status[r["status"]] = by_status.get(r["status"], 0) + r["cnt"]
            agent_key = f"{r['agent_id']}:{r['event_type']}"
            by_agent[agent_key] = by_agent.get(agent_key, 0) + r["cnt"]

        date_str = table_name.replace("audit_", "")
        summary = DailySummary(date_str, total, by_type, by_status, by_agent)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, ensure_ascii=False)

    def _drop_table(self, conn, table_name: str) -> None:
        """Drop a daily table."""
        conn.execute(f"DROP TABLE IF EXISTS [{table_name}]")
        conn.commit()

    def get_compression_ratio(self) -> float:
        """Calculate storage savings from warm compression."""
        db_path = self._db_dir / "audit_log.db"
        db_size = db_path.stat().st_size if db_path.exists() else 0

        warm_size = 0
        if self._warm_dir.exists():
            for f in self._warm_dir.glob("*.json.gz"):
                warm_size += f.stat().st_size

        if db_size == 0:
            return 0.0
        return 1.0 - (warm_size / db_size) if db_size > 0 else 0.0

    def list_summaries(self) -> list[dict]:
        """List all cold storage summaries."""
        summaries = []
        if not self._cold_dir.exists():
            return summaries
        for f in sorted(self._cold_dir.glob("*_summary.json")):
            with open(f, "r", encoding="utf-8") as fh:
                summaries.append(json.load(fh))
        return summaries
