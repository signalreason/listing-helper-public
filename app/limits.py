"""Single-process limits sized for the two-user household workload."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager


class LimitReached(Exception):
    def __init__(self, retry_after: int, message: str) -> None:
        self.retry_after = retry_after
        self.message = message


class GenerationLimits:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._starts: dict[int, deque[float]] = defaultdict(deque)
        self._active_users: set[int] = set()
        self._active_total = 0

    @contextmanager
    def start(self, user_id: int):
        with self._lock:
            now = self._clock()
            starts = self._starts[user_id]
            while starts and starts[0] <= now - 86_400:
                starts.popleft()
            hourly = [value for value in starts if value > now - 3_600]
            if len(hourly) >= 24:
                raise LimitReached(
                    3_600, "You have generated 24 listings this hour. Try again later."
                )
            if len(starts) >= 30:
                raise LimitReached(
                    86_400, "You have generated 30 listings today. Try again tomorrow."
                )
            if user_id in self._active_users:
                raise LimitReached(10, "Your listing is already being generated.")
            if self._active_total >= 2:
                raise LimitReached(10, "Both generation slots are busy. Try again in a moment.")
            starts.append(now)
            self._active_users.add(user_id)
            self._active_total += 1
        try:
            yield
        finally:
            with self._lock:
                self._active_users.discard(user_id)
                self._active_total -= 1


class LoginLimits:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._pairs: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._ips: dict[str, deque[float]] = defaultdict(deque)

    def check(self, email: str, ip: str) -> None:
        with self._lock:
            now = self._clock()
            pair = self._pairs[(email, ip)]
            ip_events = self._ips[ip]
            self._prune(pair, now - 900)
            self._prune(ip_events, now - 3_600)
            if len(pair) >= 10 or len(ip_events) >= 30:
                raise LimitReached(900, "Too many sign-in attempts. Try again in 15 minutes.")

    def failure(self, email: str, ip: str) -> None:
        with self._lock:
            now = self._clock()
            self._pairs[(email, ip)].append(now)
            self._ips[ip].append(now)

    def success(self, email: str, ip: str) -> None:
        with self._lock:
            self._pairs.pop((email, ip), None)

    @staticmethod
    def _prune(events: deque[float], cutoff: float) -> None:
        while events and events[0] <= cutoff:
            events.popleft()
