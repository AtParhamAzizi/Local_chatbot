"""Bounded, process-local abuse controls for the single serving worker.

Admission checks deliberately avoid database audit decorators so that they do
not amplify rejected traffic into additional database writes.
"""

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from functools import wraps
from ipaddress import ip_address

from flask import current_app, g, jsonify, make_response, request


@dataclass
class ClientState:
    attempts: deque = field(default_factory=deque)
    active: int = 0


class ClientLimiter:
    """Sliding-window admission with bounded keys and optional concurrency caps."""

    def __init__(self, limit, window, *, concurrent=1, max_clients=4096, clock=time.monotonic):
        if min(limit, window, concurrent, max_clients) <= 0:
            raise ValueError('Abuse limits must be positive.')
        self.limit = limit
        self.window = window
        self.concurrent = concurrent
        self.max_clients = max_clients
        self.clock = clock
        self.clients = {}
        self.lock = threading.Lock()

    def _expire(self, state, now):
        while state.attempts and state.attempts[0] <= now - self.window:
            state.attempts.popleft()

    def acquire(self, client):
        """Return zero on admission, otherwise a positive retry delay in seconds."""
        with self.lock:
            now = self.clock()
            state = self.clients.get(client)
            if state is None:
                if len(self.clients) >= self.max_clients:
                    for identity, existing in list(self.clients.items()):
                        self._expire(existing, now)
                        if not existing.attempts and not existing.active:
                            del self.clients[identity]
                    if len(self.clients) >= self.max_clients:
                        deadlines = [
                            existing.attempts[-1] + self.window - now
                            for existing in self.clients.values() if existing.attempts
                        ]
                        return max(1, math.ceil(min(deadlines))) if deadlines else 5
                state = self.clients[client] = ClientState()
            self._expire(state, now)
            if len(state.attempts) >= self.limit:
                return max(1, math.ceil(state.attempts[0] + self.window - now))
            if state.active >= self.concurrent:
                return 5
            state.attempts.append(now)
            state.active += 1
            return 0

    def release(self, client):
        with self.lock:
            state = self.clients.get(client)
            if state is not None and state.active:
                state.active -= 1


def client_identity():
    address = request.remote_addr or 'unknown'
    try:
        parsed = ip_address(address)
        return str(getattr(parsed, 'ipv4_mapped', None) or parsed)
    except ValueError:
        return 'unknown'


def retry_message(retry_after):
    return f'Too many requests. Please retry in {retry_after} seconds.'


def rate_limited_response(retry_after, body=None):
    if body is None:
        body = jsonify(error=retry_message(retry_after), code='rate_limited', retry_after=retry_after)
    response = make_response(body, 429)
    response.headers['Retry-After'] = str(retry_after)
    response.headers['Cache-Control'] = 'no-store'
    return response


def check_request_limit(group):
    limiter = current_app.extensions['abuse_controls'][group]
    client = client_identity()
    retry_after = limiter.acquire(client)
    if not retry_after:
        g.setdefault('abuse_leases', []).append((limiter, client))
    return retry_after


def limit_requests(group, *, methods=None, on_limit=None):
    def decorate(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            if methods is None or request.method in methods:
                retry_after = check_request_limit(group)
                if retry_after:
                    body = on_limit(retry_after) if on_limit else None
                    return rate_limited_response(retry_after, body)
            return function(*args, **kwargs)
        return guarded
    return decorate


def configure_abuse_controls(app, *, service):
    if 'abuse_controls' in app.extensions:
        return
    policies = {'login': (5, 60), 'upload': (6, 300)} if service == 'admin' else {
        'chat': (20, 60), 'voice': (6, 60), 'voice_health': (60, 60),
        'tts': (30, 60), 'tts_health': (60, 60),
    }
    app.extensions['abuse_controls'] = {
        group: ClientLimiter(limit, window) for group, (limit, window) in policies.items()
    }

    @app.teardown_request
    def release_request_limits(error):
        for limiter, client in g.pop('abuse_leases', []):
            limiter.release(client)
