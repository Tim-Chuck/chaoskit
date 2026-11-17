from fnmatch import fnmatchcase
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Rule(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    path: str = Field(pattern=r"^/[a-zA-Z0-9_./*~-]*$", max_length=240)
    method: Literal["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE", "*"] = "GET"
    fault: Literal["delay", "status", "abort", "timeout"]
    value: int = 0
    priority: int = Field(default=10, ge=0, le=9999)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_fault(self):
        limits = {"status": (400, 599), "delay": (1, 15000), "timeout": (1, 15000), "abort": (0, 0)}
        low, high = limits[self.fault]
        if not low <= self.value <= high:
            raise ValueError(f"{self.fault} value must be between {low} and {high}")
        if any("**" in part and part != "**" for part in self.path[1:].split("/")):
            raise ValueError("** must occupy a whole path segment")
        return self


@lru_cache(maxsize=256)
def segments(path):
    return tuple(path[1:].split("/"))


def matches_path(pattern, path):
    parts = path[1:].split("/")
    positions = {0}
    for glob in segments(pattern):
        if not positions:
            return False
        if glob == "**":
            positions = set(range(min(positions), len(parts) + 1))
        else:
            positions = {index + 1 for index in positions if index < len(parts) and fnmatchcase(parts[index], glob)}
    return len(parts) in positions


def rank(rule):
    segments = [segment for segment in rule.path.split("/") if segment]
    return (
        -sum("*" not in segment for segment in segments),
        sum(segment == "**" for segment in segments),
        sum(segment.count("*") for segment in segments),
        -len(rule.path.replace("*", "")),
        rule.priority,
        rule.id,
    )


def match(rules, method, path):
    candidates = [
        rule for rule in rules
        if rule.enabled and rule.method in (method, "*") and matches_path(rule.path, path)
    ]
    if not candidates:
        return None, "No enabled rule matched"
    winner = min(candidates, key=rank)
    reason = f"{len(candidates)} match(es) · ordered by path specificity, priority, ID"
    return winner, reason
