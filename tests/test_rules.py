import pytest
from pydantic import ValidationError

from chaoskit.rules import Rule, match, matches_path


def rule(rule_id, path, priority=10, **kwargs):
    return Rule(id=rule_id, path=path, fault="status", value=500, priority=priority, **kwargs)


@pytest.mark.parametrize(("pattern", "path", "expected"), [
    ("/api/orders/**", "/api/orders/1", True),
    ("/api/orders/**", "/api/orders", True),
    ("/api/orders/**", "/api/orders/1/sub", True),
    ("/api/*", "/api/orders/1", False),
    ("/api/*", "/apis/orders", False),
    ("/api/**/total", "/api/total", True),
    ("/api/v1.0", "/api/v1-0", False),
])
def test_path_glob(pattern, path, expected):
    assert matches_path(pattern, path) is expected


def test_specificity_then_priority_then_stable_id():
    rules = [
        rule("wide", "/api/**", 0),
        rule("nested", "/api/orders/**", 5),
        rule("z-exact", "/api/orders/1", 12),
        rule("b-exact", "/api/orders/1", 10),
        rule("a-exact", "/api/orders/1", 10),
        rule("disabled", "/api/orders/1", 0, enabled=False),
        rule("other-method", "/api/orders/1", 0, method="POST"),
    ]
    winner, reason = match(rules, "GET", "/api/orders/1")
    assert winner.id == "a-exact"
    assert "5 match(es)" in reason
    assert match(rules[:2], "GET", "/api/orders/10")[0].id == "nested"


def test_invalid_fault_value():
    with pytest.raises(ValidationError):
        Rule(id="invalid", path="/api/**", fault="timeout", value=15001)
