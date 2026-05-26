"""Tests for audit log engine, model, instrumentation, and API."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from audit_log.engine.sqlite_engine import AuditStorageEngine
from audit_log.models.event import AuditEvent, EventType, EventStatus


# ═══════════════════════════════════════════════════════════════════ #
# Model Tests
# ═══════════════════════════════════════════════════════════════════ #

def test_model_creation():
    """Test basic AuditEvent creation with defaults."""
    e = AuditEvent()
    assert e.event_id
    assert e.timestamp
    assert e.event_type == EventType.EXECUTION.value
    assert e.status == EventStatus.SUCCESS.value
    assert e.duration_ms == 0.0
    print("  ✓ test_model_creation")


def test_model_full_fields():
    """Test AuditEvent with all fields populated."""
    e = AuditEvent(
        agent_id="agent-1",
        agent_type="codex",
        session_id="session-1",
        trace_id="trace-1",
        parent_trace_id="parent-1",
        event_type=EventType.TOOL_CALL.value,
        action="test.action",
        target_type="issue",
        target_id="LGS-123",
        input_summary="input data",
        output_summary="output data",
        decision="chose A",
        status=EventStatus.FAILURE.value,
        error={"type": "ValueError", "message": "test error"},
        duration_ms=42.5,
        metadata={"key": "value"},
    )
    d = e.to_dict()
    assert d["agent_id"] == "agent-1"
    assert d["event_type"] == "tool_call"
    assert d["status"] == "failure"
    assert d["duration_ms"] == 42.5
    assert d["error"]["type"] == "ValueError"
    print("  ✓ test_model_full_fields")


def test_model_serialization_roundtrip():
    """Test to_dict / from_dict roundtrip."""
    e = AuditEvent(agent_id="agent-1", action="test", duration_ms=12.3)
    d = e.to_dict()
    e2 = AuditEvent.from_dict(d)
    assert e.event_id == e2.event_id
    assert e.agent_id == e2.agent_id
    assert e.duration_ms == e2.duration_ms
    assert e.timestamp == e2.timestamp
    print("  ✓ test_model_serialization_roundtrip")


def test_model_no_utcnow():
    """Verify datetime.now(timezone.utc) is used, not utcnow()."""
    import inspect
    source = inspect.getsource(AuditEvent)
    assert "utcnow()" not in source, "datetime.utcnow() is deprecated in Python 3.12+"
    assert "timezone.utc" in source
    print("  ✓ test_model_no_utcnow")


# ═══════════════════════════════════════════════════════════════════ #
# Engine Tests
# ═══════════════════════════════════════════════════════════════════ #

def _make_engine():
    """Create a temporary engine for testing."""
    tmp = tempfile.mkdtemp()
    return AuditStorageEngine(db_dir=tmp)


def test_engine_write_and_read():
    """Test basic write and get_event."""
    engine = _make_engine()
    e = AuditEvent(agent_id="agent-1", event_type=EventType.TOOL_CALL.value, action="test")
    engine.write_event(e)

    fetched = engine.get_event(e.event_id)
    assert fetched is not None
    assert fetched.event_id == e.event_id
    assert fetched.agent_id == "agent-1"

    engine.close()
    print("  ✓ test_engine_write_and_read")


def test_engine_query():
    """Test query_events with SQL-level filters."""
    engine = _make_engine()

    e1 = AuditEvent(agent_id="agent-1", event_type=EventType.TOOL_CALL.value, action="read.file", status=EventStatus.SUCCESS.value)
    e2 = AuditEvent(agent_id="agent-2", event_type=EventType.ERROR.value, action="write.file", status=EventStatus.FAILURE.value)
    e3 = AuditEvent(agent_id="agent-1", event_type=EventType.TOOL_CALL.value, action="delete.file", status=EventStatus.SUCCESS.value)
    for e in [e1, e2, e3]:
        engine.write_event(e)

    # Filter by agent_id
    results = engine.query_events(agent_id="agent-1")
    assert len(results) == 2

    # Filter by event_type
    results = engine.query_events(event_type=EventType.ERROR.value)
    assert len(results) == 1
    assert results[0].event_type == "error"

    # Filter by action (SQL LIKE)
    results = engine.query_events(action="read")
    assert len(results) == 1

    # Filter by status
    results = engine.query_events(status=EventStatus.FAILURE.value)
    assert len(results) == 1

    # Multi-filter
    results = engine.query_events(agent_id="agent-1", event_type=EventType.TOOL_CALL.value)
    assert len(results) == 2

    engine.close()
    print("  ✓ test_engine_query")


def test_engine_trace_chain():
    """Test get_trace_chain includes child traces."""
    engine = _make_engine()
    trace_id = "trace-root"

    e1 = AuditEvent(trace_id=trace_id, action="root.call")
    e2 = AuditEvent(trace_id="child-1", parent_trace_id=trace_id, action="child.call")
    e3 = AuditEvent(trace_id=trace_id, action="root.call.2")
    for e in [e1, e2, e3]:
        engine.write_event(e)

    chain = engine.get_trace_chain(trace_id)
    assert len(chain) == 3  # root + child + root

    engine.close()
    print("  ✓ test_engine_trace_chain")


def test_engine_session_replay():
    """Test get_session_replay returns all session events."""
    engine = _make_engine()
    session_id = "session-1"

    e1 = AuditEvent(session_id=session_id, action="step.1", timestamp="2026-01-01T00:00:01Z")
    e2 = AuditEvent(session_id=session_id, action="step.2", timestamp="2026-01-01T00:00:02Z")
    e3 = AuditEvent(session_id="other-session", action="other")
    for e in [e1, e2, e3]:
        engine.write_event(e)

    replay = engine.get_session_replay(session_id)
    assert len(replay) == 2
    # query_events returns DESC by default, get_session_replay returns chronological
    actions = [e.action for e in replay]
    assert "step.1" in actions
    assert "step.2" in actions

    engine.close()
    print("  ✓ test_engine_session_replay")


def test_engine_error_stats():
    """Test get_error_stats aggregation."""
    engine = _make_engine()

    for i in range(5):
        e = AuditEvent(agent_id=f"agent-{i % 2}", event_type=EventType.TOOL_CALL.value,
                       status=EventStatus.FAILURE.value, action="fail")
        engine.write_event(e)

    stats = engine.get_error_stats()
    assert len(stats) > 0
    total = sum(s["count"] for s in stats)
    assert total == 5

    engine.close()
    print("  ✓ test_engine_error_stats")


def test_engine_batch_write():
    """Test write_events_batch."""
    engine = _make_engine()
    events = [AuditEvent(agent_id=f"agent-{i}", action=f"action.{i}") for i in range(10)]
    engine.write_events_batch(events)

    results = engine.query_events(limit=100)
    assert len(results) == 10

    engine.close()
    print("  ✓ test_engine_batch_write")


def test_engine_get_event_count():
    """Test get_event_count."""
    engine = _make_engine()
    for i in range(5):
        engine.write_event(AuditEvent(agent_id=f"agent-{i}", action="test"))

    count = engine.get_event_count()
    assert count >= 5

    engine.close()
    print("  ✓ test_engine_get_event_count")


def test_engine_export():
    """Test export_all returns all events."""
    engine = _make_engine()
    for i in range(3):
        engine.write_event(AuditEvent(agent_id=f"agent-{i}", action="test"))

    exported = engine.export_all()
    assert len(exported) == 3

    engine.close()
    print("  ✓ test_engine_export")


# ═══════════════════════════════════════════════════════════════════ #
# Instrumentation Tests
# ═══════════════════════════════════════════════════════════════════ #

def test_instrumentation_import():
    """Verify instrumentation module imports cleanly."""
    from audit_log import instrumentation
    assert hasattr(instrumentation, "AuditContext")
    assert hasattr(instrumentation, "audit_tool_call")
    assert hasattr(instrumentation, "audit_decision")
    assert hasattr(instrumentation, "AsyncAuditWriter")
    print("  ✓ test_instrumentation_import")


def test_instrumentation_context():
    """Test AuditContext creates session events."""
    from audit_log.instrumentation import AuditContext

    engine = _make_engine()
    with AuditContext(engine, agent_id="test-agent", agent_type="codex"):
        pass

    # Should have session.start + session.end
    count = engine.get_event_count()
    assert count >= 2

    engine.close()
    print("  ✓ test_instrumentation_context")


def test_instrumentation_decorator_sync():
    """Test @audit_tool_call decorator on sync function."""
    from audit_log.instrumentation import AuditContext, audit_tool_call

    engine = _make_engine()

    @audit_tool_call(engine)
    def my_func(x: int) -> int:
        return x * 2

    with AuditContext(engine, agent_id="test-agent", agent_type="codex"):
        result = my_func(21)

    assert result == 42
    count = engine.get_event_count()
    assert count >= 2

    engine.close()
    print("  ✓ test_instrumentation_decorator_sync")


def test_instrumentation_decorator_error():
    """Test @audit_tool_call captures exceptions."""
    from audit_log.instrumentation import AuditContext, audit_tool_call

    engine = _make_engine()

    @audit_tool_call(engine)
    def failing_func():
        raise ValueError("test error")

    with AuditContext(engine, agent_id="test-agent", agent_type="codex"):
        try:
            failing_func()
        except ValueError:
            pass

    # Should have tool_call + error events
    count = engine.get_event_count()
    assert count >= 2

    engine.close()
    print("  ✓ test_instrumentation_decorator_error")


def test_instrumentation_manual_functions():
    """Test manual logging functions."""
    from audit_log.instrumentation import AuditContext, audit_state_change, audit_error, audit_comment

    engine = _make_engine()

    with AuditContext(engine, agent_id="test-agent", agent_type="codex"):
        audit_state_change(engine, "issue", "LGS-123", "todo", "in_progress", "Starting work")
        audit_error(engine, "test.action", "ValueError", "something went wrong")
        audit_comment(engine, "LGS-123", "test comment")

    count = engine.get_event_count()
    assert count >= 5  # session start + end + 3 manual

    engine.close()
    print("  ✓ test_instrumentation_manual_functions")


# ═══════════════════════════════════════════════════════════════════ #
# Main
# ═══════════════════════════════════════════════════════════════════ #

def run_all():
    print(f"\n{'='*60}")
    print("Audit Log Test Suite")
    print(f"{'='*60}\n")

    tests = [
        test_model_creation,
        test_model_full_fields,
        test_model_serialization_roundtrip,
        test_model_no_utcnow,
        test_engine_write_and_read,
        test_engine_query,
        test_engine_trace_chain,
        test_engine_session_replay,
        test_engine_error_stats,
        test_engine_batch_write,
        test_engine_get_event_count,
        test_engine_export,
        test_instrumentation_import,
        test_instrumentation_context,
        test_instrumentation_decorator_sync,
        test_instrumentation_decorator_error,
        test_instrumentation_manual_functions,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    print(f"{'='*60}\n")
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
