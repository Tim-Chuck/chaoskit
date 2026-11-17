import secrets
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import HTTPException

from .rules import Rule


@dataclass
class Run:
    rule: Rule
    until: float
    events: deque = field(default_factory=lambda: deque(maxlen=100))


class Runs:
    def __init__(self):
        self.active = {}

    def create(self, rule):
        now = time.monotonic()
        self.active = {key: run for key, run in self.active.items() if run.until > now}
        if len(self.active) >= 32:
            raise HTTPException(429, "Too many active checks")
        key = secrets.token_urlsafe(24)
        self.active[key] = Run(rule, now + 120)
        return key

    def get(self, key):
        run = self.active.get(key)
        if run is None or run.until <= time.monotonic():
            raise HTTPException(404, "Check expired or unknown")
        return run

    def delete(self, key):
        self.active.pop(key, None)
