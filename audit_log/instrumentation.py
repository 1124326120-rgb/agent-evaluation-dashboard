"""Agent instrumentation layer: decorators, context manager, async writer.

Automatically captures audit events at key agent operation points
without manual invocation.
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from queue import Queue
from typing import Any, Callable, Optional

from audit_log.engine.sqlite_engine import AuditStorageEngine
from audit_log.models.event import AuditEvent, EventType, EventStatus

# Context variables for session/trace propagation
_current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
_current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")
_current_agent_id: ContextVar[str] = ContextVar("current_agent_id", default="")
_current_agent_type: ContextVar[str] = ContextVar("current_agent_type", default="")


def get_current_session_id() -> str:
    return _current_session_id.get()


def get_current_trace_id() -> str:
    return _current_trace_id.get()


def get_current_agent_id() -> str:
    return _current_agent_id.get()


# ------------------------------------------------------------------ #
# Audit Context Manager
# ------------------------------------------------------------------ #


class AuditContext:
    """Context manager that sets up session and trace IDs for an agent run.

    Usage:
        with AuditContext(engine, agent_id="agent-1", agent_type="codex"):
            # Agent operations here are automatically tracked
            ...
    """

    def __init__(self, engine: AuditStorageEngine,
                 agent_id: str = "",
                 agent_type: str = "",
                 parent_trace_id: Optional[str] = None):
        self._engine = engine
        self._session_id = str(uuid.uuid4())
        self._trace_id = str(uuid.uuid4())
        self._agent_id = agent_id
        self._agent_type = agent_type
        self._parent_trace_id = parent_trace_id or ""
        self._token_session = None
        self._token_trace = None
        self._token_agent_id = None
        self._token_agent_type = None
        self._start_time: Optional[float] = None

    def __enter__(self) -> "AuditContext":
        self._start_time = time.monotonic()
        self._token_session = _current_session_id.set(self._session_id)
        self._token_trace = _current_trace_id.set(self._trace_id)
        self._token_agent_id = _current_agent_id.set(self._agent_id)
        self._token_agent_type = _current_agent_type.set(self._agent_type)

        # Log session start
        event = AuditEvent(
            agent_id=self._agent_id,
            agent_type=self._agent_type,
            session_id=self._session_id,
            trace_id=self._trace_id,
            parent_trace_id=self._parent_trace_id or None,
            event_type=EventType.SYSTEM.value,
            action="session.start",
            status=EventStatus.SUCCESS.value,
        )
        self._engine.write_event(event)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        duration = (time.monotonic() - self._start_time) * 1000 if self._start_time else 0
        status = EventStatus.FAILURE.value if exc_type else EventStatus.SUCCESS.value

        event = AuditEvent(
            agent_id=self._agent_id,
            agent_type=self._agent_type,
            session_id=self._session_id,
            trace_id=self._trace_id,
            event_type=EventType.SYSTEM.value,
            action="session.end",
            status=status,
            duration_ms=duration,
            error={"type": str(exc_type.__name__), "message": str(exc_val)} if exc_type else None,
        )
        self._engine.write_event(event)

        # Reset context vars
        if self._token_session:
            _current_session_id.reset(self._token_session)
        if self._token_trace:
            _current_trace_id.reset(self._token_trace)
        if self._token_agent_id:
            _current_agent_id.reset(self._token_agent_id)
        if self._token_agent_type:
            _current_agent_type.reset(self._token_agent_type)


# ------------------------------------------------------------------ #
# Decorators
# ------------------------------------------------------------------ #


def audit_tool_call(engine: AuditStorageEngine):
    """Decorator that automatically records tool call audit events.

    Supports both sync and async functions.

    Usage:
        @audit_tool_call(engine)
        def my_tool(arg1, arg2):
            ...
    """
    def decorator(func: Callable) -> Callable:
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start = time.monotonic()
                action = f"{func.__module__}.{func.__name__}" if func.__module__ != "__main__" else func.__name__
                session_id = _current_session_id.get()
                trace_id = _current_trace_id.get()
                agent_id = _current_agent_id.get()
                agent_type = _current_agent_type.get()

                # Build input summary from first arg or kwargs
                input_summary = _summarize_args(args, kwargs)

                # Write tool_call event
                call_event = AuditEvent(
                    agent_id=agent_id,
                    agent_type=agent_type,
                    session_id=session_id,
                    trace_id=trace_id,
                    event_type=EventType.TOOL_CALL.value,
                    action=action,
                    input_summary=input_summary,
                    status=EventStatus.PENDING.value,
                )
                engine.write_event(call_event)

                try:
                    result = await func(*args, **kwargs)
                    duration = (time.monotonic() - start) * 1000
                    # Write tool_result event
                    result_event = AuditEvent(
                        agent_id=agent_id,
                        agent_type=agent_type,
                        session_id=session_id,
                        trace_id=trace_id,
                        event_type=EventType.TOOL_CALL.value,
                        action=action,
                        input_summary=input_summary,
                        output_summary=_summarize_output(result),
                        status=EventStatus.SUCCESS.value,
                        duration_ms=duration,
                    )
                    engine.write_event(result_event)
                    return result
                except Exception as e:
                    duration = (time.monotonic() - start) * 1000
                    error_event = AuditEvent(
                        agent_id=agent_id,
                        agent_type=agent_type,
                        session_id=session_id,
                        trace_id=trace_id,
                        event_type=EventType.ERROR.value,
                        action=action,
                        input_summary=input_summary,
                        status=EventStatus.FAILURE.value,
                        duration_ms=duration,
                        error={"type": type(e).__name__, "message": str(e)},
                    )
                    engine.write_event(error_event)
                    raise

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                start = time.monotonic()
                action = f"{func.__module__}.{func.__name__}" if func.__module__ != "__main__" else func.__name__
                session_id = _current_session_id.get()
                trace_id = _current_trace_id.get()
                agent_id = _current_agent_id.get()
                agent_type = _current_agent_type.get()
                input_summary = _summarize_args(args, kwargs)

                call_event = AuditEvent(
                    agent_id=agent_id,
                    agent_type=agent_type,
                    session_id=session_id,
                    trace_id=trace_id,
                    event_type=EventType.TOOL_CALL.value,
                    action=action,
                    input_summary=input_summary,
                    status=EventStatus.PENDING.value,
                )
                engine.write_event(call_event)

                try:
                    result = func(*args, **kwargs)
                    duration = (time.monotonic() - start) * 1000
                    result_event = AuditEvent(
                        agent_id=agent_id,
                        agent_type=agent_type,
                        session_id=session_id,
                        trace_id=trace_id,
                        event_type=EventType.TOOL_CALL.value,
                        action=action,
                        input_summary=input_summary,
                        output_summary=_summarize_output(result),
                        status=EventStatus.SUCCESS.value,
                        duration_ms=duration,
                    )
                    engine.write_event(result_event)
                    return result
                except Exception as e:
                    duration = (time.monotonic() - start) * 1000
                    error_event = AuditEvent(
                        agent_id=agent_id,
                        agent_type=agent_type,
                        session_id=session_id,
                        trace_id=trace_id,
                        event_type=EventType.ERROR.value,
                        action=action,
                        input_summary=input_summary,
                        status=EventStatus.FAILURE.value,
                        duration_ms=duration,
                        error={"type": type(e).__name__, "message": str(e)},
                    )
                    engine.write_event(error_event)
                    raise

            return sync_wrapper

    return decorator


def audit_decision(engine: AuditStorageEngine):
    """Decorator that records decision audit events.

    Usage:
        @audit_decision(engine)
        def choose_strategy(context):
            ...
    """
    def decorator(func: Callable) -> Callable:
        is_async = asyncio.iscoroutinefunction(func)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.monotonic()
            action = f"decision.{func.__name__}"
            session_id = _current_session_id.get()
            trace_id = _current_trace_id.get()
            agent_id = _current_agent_id.get()
            agent_type = _current_agent_type.get()
            input_summary = _summarize_args(args, kwargs)

            try:
                result = func(*args, **kwargs)
                duration = (time.monotonic() - start) * 1000
                decision_text = str(result)[:256] if result else ""
                event = AuditEvent(
                    agent_id=agent_id,
                    agent_type=agent_type,
                    session_id=session_id,
                    trace_id=trace_id,
                    event_type=EventType.DECISION.value,
                    action=action,
                    input_summary=input_summary,
                    output_summary=decision_text,
                    decision=decision_text,
                    status=EventStatus.SUCCESS.value,
                    duration_ms=duration,
                )
                engine.write_event(event)
                return result
            except Exception as e:
                duration = (time.monotonic() - start) * 1000
                error_event = AuditEvent(
                    agent_id=agent_id,
                    agent_type=agent_type,
                    session_id=session_id,
                    trace_id=trace_id,
                    event_type=EventType.DECISION.value,
                    action=action,
                    input_summary=input_summary,
                    status=EventStatus.FAILURE.value,
                    duration_ms=duration,
                    error={"type": type(e).__name__, "message": str(e)},
                )
                engine.write_event(error_event)
                raise

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                return sync_wrapper(*args, **kwargs)
            return async_wrapper

        return sync_wrapper
    return decorator


# ------------------------------------------------------------------ #
# Manual logging functions
# ------------------------------------------------------------------ #


def audit_event_sync(engine: AuditStorageEngine,
                     event_type: str,
                     action: str,
                     status: str = EventStatus.SUCCESS.value,
                     input_summary: str = "",
                     output_summary: str = "",
                     duration_ms: float = 0.0,
                     error: Optional[dict] = None,
                     decision: Optional[str] = None,
                     target_type: str = "",
                     target_id: str = "",
                     metadata: Optional[dict] = None) -> None:
    """Manually log a generic audit event."""
    event = AuditEvent(
        agent_id=_current_agent_id.get(),
        agent_type=_current_agent_type.get(),
        session_id=_current_session_id.get(),
        trace_id=_current_trace_id.get(),
        event_type=event_type,
        action=action,
        input_summary=input_summary,
        output_summary=output_summary,
        status=status,
        duration_ms=duration_ms,
        error=error,
        decision=decision,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata or {},
    )
    engine.write_event(event)


async def audit_event_async(engine: AuditStorageEngine,
                            event_type: str,
                            action: str,
                            status: str = EventStatus.SUCCESS.value,
                            input_summary: str = "",
                            output_summary: str = "",
                            duration_ms: float = 0.0,
                            error: Optional[dict] = None,
                            decision: Optional[str] = None,
                            target_type: str = "",
                            target_id: str = "",
                            metadata: Optional[dict] = None) -> None:
    """Async version of manual audit event logging."""
    audit_event_sync(engine, event_type, action, status, input_summary,
                     output_summary, duration_ms, error, decision,
                     target_type, target_id, metadata)


def audit_state_change(engine: AuditStorageEngine,
                       target_type: str,
                       target_id: str,
                       old_state: str,
                       new_state: str,
                       reason: str = "") -> None:
    """Log a state change."""
    audit_event_sync(
        engine=engine,
        event_type=EventType.EXECUTION.value,
        action="state.change",
        input_summary=f"{target_type}:{target_id} {old_state} -> {new_state}",
        output_summary=reason or f"Changed from {old_state} to {new_state}",
        status=EventStatus.SUCCESS.value,
        target_type=target_type,
        target_id=target_id,
    )


def audit_error(engine: AuditStorageEngine,
                action: str,
                error_type: str,
                error_message: str,
                input_summary: str = "") -> None:
    """Log an error event."""
    audit_event_sync(
        engine=engine,
        event_type=EventType.ERROR.value,
        action=action,
        input_summary=input_summary,
        status=EventStatus.FAILURE.value,
        error={"type": error_type, "message": error_message},
    )


def audit_comment(engine: AuditStorageEngine,
                  target_id: str,
                  summary: str) -> None:
    """Log a comment event."""
    audit_event_sync(
        engine=engine,
        event_type=EventType.EXECUTION.value,
        action="comment.post",
        output_summary=summary,
        target_type="issue",
        target_id=target_id,
    )


# ------------------------------------------------------------------ #
# Async batch writer (non-blocking)
# ------------------------------------------------------------------ #


class AsyncAuditWriter:
    """Non-blocking batch audit writer.

    Accumulates events in a thread-safe queue and flushes them
    to the engine in batches at a configurable interval or size.
    """

    def __init__(self, engine: AuditStorageEngine,
                 flush_interval: float = 0.5,
                 batch_size: int = 100):
        self._engine = engine
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._queue: Queue = Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background flush thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background flush thread and flush remaining events."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._flush_now()

    def enqueue(self, event: AuditEvent) -> None:
        """Add an event to the write queue (non-blocking)."""
        self._queue.put_nowait(event)

    def _flush_now(self) -> None:
        """Flush all queued events to the engine."""
        batch: list[AuditEvent] = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except Exception:
                break
            if len(batch) >= self._batch_size:
                self._engine.write_events_batch(batch)
                batch.clear()
        if batch:
            self._engine.write_events_batch(batch)

    def _flush_loop(self) -> None:
        """Background loop: flush periodically."""
        while self._running:
            import time as _time
            _time.sleep(self._flush_interval)
            self._flush_now()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _summarize_args(args: tuple, kwargs: dict, max_len: int = 256) -> str:
    """Build a short summary of function arguments."""
    parts = []
    for i, a in enumerate(args):
        s = str(a)[:64]
        parts.append(f"arg{i}={s}")
    for k, v in kwargs.items():
        s = str(v)[:64]
        parts.append(f"{k}={s}")
    combined = ", ".join(parts)
    return combined[:max_len]


def _summarize_output(result: Any, max_len: int = 256) -> str:
    """Build a short summary of function output."""
    if result is None:
        return ""
    s = str(result)
    return s[:max_len]
