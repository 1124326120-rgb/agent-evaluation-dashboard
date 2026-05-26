"""FastAPI-based audit log query API with authentication.

All filters applied at SQL level. Uses datetime.now(timezone.utc)
instead of deprecated datetime.utcnow().
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from audit_log.engine.lifetime_manager import LifetimeManager
from audit_log.engine.sqlite_engine import AuditStorageEngine
from audit_log.models.event import AuditEvent, EventType, EventStatus

app = FastAPI(title="Audit Log API", version="1.0.0")

# ── Configuration ────────────────────────────────────────────────── #
BASE_DIR = Path(__file__).parent
DB_DIR = BASE_DIR / "audit_data"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Ensure directories
DB_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# ── Auth config ──────────────────────────────────────────────────── #
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "false").lower() in ("true", "1", "yes")
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "admin")

# ── Global instances ─────────────────────────────────────────────── #
engine = AuditStorageEngine(db_dir=str(DB_DIR))
lifetime_manager = LifetimeManager(db_dir=str(DB_DIR))
lifetime_manager.start()

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ── Auth helper ──────────────────────────────────────────────────── #


def verify_auth(request: Request) -> bool:
    """Verify HTTP Basic Auth credentials."""
    if not AUTH_ENABLED:
        return True
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    import base64
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
        return username == AUTH_USERNAME and password == AUTH_PASSWORD
    except Exception:
        return False


def require_auth(request: Request):
    """FastAPI dependency for auth."""
    if not verify_auth(request):
        import base64
        auth_header = "Basic realm=\"Audit Log API\""
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": auth_header},
        )


# ── REST API Endpoints ───────────────────────────────────────────── #


@app.get("/api/events")
def list_events(
    request: Request,
    start_time: Optional[str] = Query(None, description="ISO8601 start time"),
    end_time: Optional[str] = Query(None, description="ISO8601 end time"),
    agent_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    """Query audit events with multi-dimensional filtering.

    All filters are applied at the SQL level via the storage engine.
    """
    require_auth(request)
    events = engine.query_events(
        start_time=start_time,
        end_time=end_time,
        agent_id=agent_id,
        event_type=event_type,
        session_id=session_id,
        trace_id=trace_id,
        status=status,
        action=action,
        limit=limit,
        offset=offset,
    )
    total = engine.get_event_count(start_time=start_time, end_time=end_time)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "events": [e.to_dict() for e in events],
    }


@app.get("/api/events/{event_id}")
def get_event(request: Request, event_id: str):
    """Get a single event by ID."""
    require_auth(request)
    event = engine.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event.to_dict()


@app.get("/api/sessions/{session_id}/replay")
def session_replay(request: Request, session_id: str):
    """Replay a complete session timeline."""
    require_auth(request)
    events = engine.get_session_replay(session_id)
    return {
        "session_id": session_id,
        "total_events": len(events),
        "events": [e.to_dict() for e in events],
    }


@app.get("/api/traces/{trace_id}")
def trace_chain(request: Request, trace_id: str):
    """Get a trace chain with waterfall data."""
    require_auth(request)
    events = engine.get_trace_chain(trace_id)
    waterfall = []
    for e in events:
        waterfall.append({
            "event_id": e.event_id,
            "action": e.action,
            "event_type": e.event_type,
            "agent_id": e.agent_id,
            "timestamp": e.timestamp,
            "duration_ms": e.duration_ms,
            "status": e.status,
            "trace_id": e.trace_id,
            "parent_trace_id": e.parent_trace_id,
        })
    return {
        "trace_id": trace_id,
        "total_events": len(events),
        "waterfall": waterfall,
    }


@app.get("/api/stats/errors")
def error_stats(request: Request):
    """Get aggregated error statistics."""
    require_auth(request)
    stats = engine.get_error_stats()
    return {"errors": stats}


@app.get("/api/stats/summary")
def summary_stats(request: Request):
    """Get summary statistics."""
    require_auth(request)
    now = datetime.now(timezone.utc)
    today_start = now.strftime("%Y-%m-%dT00:00:00Z")
    week_ago = (now - __import__("datetime").timedelta(days=7)).isoformat()

    total_7d = engine.get_event_count(start_time=week_ago)
    total_today = engine.get_event_count(start_time=today_start)
    compression = lifetime_manager.get_compression_ratio()

    return {
        "total_events_7d": total_7d,
        "total_events_today": total_today,
        "compression_ratio": round(compression, 4),
        "auth_enabled": AUTH_ENABLED,
    }


@app.get("/api/summaries")
def cold_summaries(request: Request):
    """List cold storage summaries."""
    require_auth(request)
    return {"summaries": lifetime_manager.list_summaries()}


# ── Export Endpoints ─────────────────────────────────────────────── #


@app.get("/api/export/csv")
def export_csv(
    request: Request,
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    """Export events as CSV."""
    require_auth(request)
    events = engine.export_all(start_time=start_time, end_time=end_time)

    output = io.StringIO()
    writer = csv.writer(output)
    # Header
    writer.writerow([
        "event_id", "timestamp", "agent_id", "agent_type", "session_id",
        "trace_id", "event_type", "action", "target_type", "target_id",
        "input_summary", "output_summary", "status", "duration_ms",
    ])
    for e in events:
        writer.writerow([
            e.event_id, e.timestamp, e.agent_id, e.agent_type, e.session_id,
            e.trace_id, e.event_type, e.action, e.target_type, e.target_id,
            e.input_summary, e.output_summary, e.status, e.duration_ms,
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_events.csv"},
    )


@app.get("/api/export/json")
def export_json(
    request: Request,
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    """Export events as JSON."""
    require_auth(request)
    events = engine.export_all(start_time=start_time, end_time=end_time)
    return Response(
        content=json.dumps([e.to_dict() for e in events], ensure_ascii=False, default=str),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit_events.json"},
    )


# ── Web UI Routes ────────────────────────────────────────────────── #


@app.get("/", response_class=HTMLResponse)
def events_page(request: Request):
    """Event list page."""
    require_auth(request)
    return templates.TemplateResponse("events.html", {"request": request})


@app.get("/session/{session_id}", response_class=HTMLResponse)
def session_page(request: Request, session_id: str):
    """Session replay page."""
    require_auth(request)
    return templates.TemplateResponse(
        "session.html", {"request": request, "session_id": session_id}
    )


@app.get("/trace/{trace_id}", response_class=HTMLResponse)
def trace_page(request: Request, trace_id: str):
    """Trace chain waterfall page."""
    require_auth(request)
    return templates.TemplateResponse(
        "trace.html", {"request": request, "trace_id": trace_id}
    )


@app.get("/errors", response_class=HTMLResponse)
def errors_page(request: Request):
    """Error aggregation panel page."""
    require_auth(request)
    return templates.TemplateResponse("errors.html", {"request": request})


# ── Static files ─────────────────────────────────────────────────── #
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Startup ──────────────────────────────────────────────────────── #


def main():
    """Run the audit log API server."""
    port = int(os.environ.get("AUDIT_PORT", "8399"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
