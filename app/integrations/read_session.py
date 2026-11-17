"""Resources and duplicate reads shared within one synchronous source-read batch."""

from __future__ import annotations

import ssl
import threading
from concurrent.futures import Future
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar

import httpx

# Public lookups fan out to at most 16 requests; retain their connections only
# until that lookup/sync batch finishes. No content survives between batches.
HTTP_CONNECTIONS = 16
_current: ContextVar[ReadSession | None] = ContextVar("source_read_session", default=None)


class ReadSession:
    def __init__(self):
        self._lock = threading.Lock()
        self._stack = ExitStack()
        self._http = None
        self._ssh = None
        self._reads: dict[tuple, Future] = {}

    def http_client(self, timeout):
        with self._lock:
            if self._http is None:
                self._http = self._stack.enter_context(
                    httpx.Client(
                        timeout=timeout,
                        verify=ssl.create_default_context(),
                        trust_env=False,
                        follow_redirects=False,
                        limits=httpx.Limits(
                            max_connections=HTTP_CONNECTIONS,
                            max_keepalive_connections=HTTP_CONNECTIONS,
                        ),
                    )
                )
            return self._http

    def ssh_connections(self):
        from app.integrations.gerrit import ReadConnections

        with self._lock:
            if self._ssh is None:
                self._ssh = self._stack.enter_context(ReadConnections())
            return self._ssh

    def memo(self, key, read):
        with self._lock:
            owner = key not in self._reads
            future = self._reads.setdefault(key, Future())
        if owner:
            try:
                future.set_result(read())
            except BaseException as exc:
                future.set_exception(exc)
        return future.result()

    def prime(self, key, value):
        with self._lock:
            if key not in self._reads:
                future = Future()
                future.set_result(value)
                self._reads[key] = future

    def run(self, function, *args, **kwargs):
        """Explicitly propagate this batch's resources to a worker thread."""
        token = _current.set(self)
        try:
            return function(*args, **kwargs)
        finally:
            _current.reset(token)

    def close(self):
        self._stack.close()
        self._reads.clear()


def current_session():
    return _current.get()


@contextmanager
def read_session():
    existing = current_session()
    if existing is not None:
        yield existing
        return
    session = ReadSession()
    token = _current.set(session)
    try:
        yield session
    finally:
        try:
            session.close()
        finally:
            _current.reset(token)


@contextmanager
def http_client(timeout):
    session = current_session()
    if session is not None:
        yield session.http_client(timeout)
    else:
        with httpx.Client(
            timeout=timeout,
            verify=ssl.create_default_context(),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            yield client
