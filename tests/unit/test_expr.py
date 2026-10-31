"""PLAN 2.16: the composite expression grammar. Good expressions evaluate; everything outside the
grammar raises ExprError, never anything else; a fuzz of generated expressions, valid and broken,
must each either evaluate or raise ExprError (ruled 2026-10-31: at least a hundred)."""

from __future__ import annotations

import math
import random
import re

import pytest

from openfactory.core.expr import MAX_DEPTH, MAX_LENGTH, ExprError, number, parse, substitute

PARAMS = {"length": 2.0, "gap": 0.2, "n": 3.0}
PORTS = {"gate.raised": True, "belt.running": False, "eye.detect": True, "dist.distance": 0.4}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("{length} - 0.2", 1.8),
        ("{length} - {gap} * 2", 1.6),
        ("({length} - {gap}) / 2", 0.9),
        ("-{gap}", -0.2),
        ("+3", 3.0),
        ("1e-3 * 1000", 1.0),
        ("{ n }", 3.0),
    ],
)
def test_numeric_fields(text, expected):
    assert number(text, PARAMS) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("gate.raised and eye.detect", True),
        ("gate.raised and belt.running", False),
        ("not belt.running", True),
        ("gate.raised or belt.running", True),
        ("dist.distance < 0.5", True),
        ("0.3 <= dist.distance < 0.35", False),
        ("eye.detect == true", True),
        ("(gate.raised and not belt.running) or false", True),
    ],
)
def test_boolean_io(text, expected):
    expr = parse(text, refs=True)
    assert expr.evaluate(PORTS) is expected


def test_refs_are_listed_in_order():
    assert parse("gate.raised and not belt.running or gate.raised", refs=True).refs == (
        "gate.raised",
        "belt.running",
    )


@pytest.mark.parametrize(
    "text, why",
    [
        ("__import__('os')", "Call"),
        ("length ** 2", "only + - * /"),
        ("length // 2", "only + - * /"),
        ("'a'", "only numbers"),
        ("[1, 2]", "List"),
        ("x[0]", "Subscript"),
        ("a.b.c", "not allowed"),
        ("lambda: 1", "Lambda"),
        ("1 if a else 2", "IfExp"),
        ("1 in a", "only < <= > >= == != compare"),
        ("~1", "unary"),
        ("1 +", "does not parse"),
        ("(" * 210 + "1" + ")" * 210, "does not parse"),  # Python's nesting limit is 200
        ("1/0", "division by zero"),
        ("1e308 * 10", "finite"),
        ("true + 1", "takes numbers"),
        ("1 and 2", "takes true/false"),
        ("not 1", "takes true/false"),
        ("true < 1", "takes numbers"),
        ("true == 1", "like with like"),
        ("unknown + 1", "unknown"),
    ],
)
def test_outside_the_grammar_raises_expr_error(text, why):
    with pytest.raises(ExprError, match=re.escape(why)):
        parse(text, refs=True).evaluate({"a": 1.0, "x": 1.0, "length": 2.0})


def test_numeric_field_errors():
    with pytest.raises(ExprError, match="unknown parameter 'width'"):
        number("{width} * 2", PARAMS)
    with pytest.raises(ExprError, match="a number is needed"):
        number("{length} > 1", PARAMS)
    with pytest.raises(ExprError, match="braces"):
        substitute("{length - 1}")
    with pytest.raises(ExprError, match="not allowed here"):
        number("gate.raised", PARAMS)  # a part.port ref is only for boolean I/O
    with pytest.raises(ExprError, match="limit"):
        parse("1+" * MAX_LENGTH + "1")
    with pytest.raises(ExprError, match="nested deeper"):
        parse("-" * (MAX_DEPTH + 1) + "1")


# --- fuzz -------------------------------------------------------------------------------------

ATOMS = ["1", "0", "2.5", "-3", "1e3", "1e400", "0.0", "true", "false", "length", "gap",
         "gate.raised", "belt.running", "dist.distance", "nope", "nope.port", "'s'", "None",
         "1j", "x[0]", "f()", "a.b.c", "{length}", "{gap}", "{length", "{}", "{ 1 }"]  # fmt: skip
BINARY = ["+", "-", "*", "/", "<", "<=", ">", ">=", "==", "!=", "and", "or", "**", "//", "%",
          "in", "is", "&", "@"]  # fmt: skip
UNARY = ["-", "+", "not ", "~"]
JUNK = ["(", ")", ",", ":", "{", "}", "lambda", "if", "  ", ".", "=", "\\", "\n", "#"]


GOOD_ATOMS = ATOMS[:13]  # numbers, true/false, parameters, ports


def generated(rng: random.Random, depth: int = 0) -> str:
    roll = rng.random()
    if depth > 4 or roll < 0.3:
        return rng.choice(GOOD_ATOMS if rng.random() < 0.85 else ATOMS)
    if roll < 0.55:
        op = rng.choice(BINARY[:12] if rng.random() < 0.85 else BINARY)
        return f"{generated(rng, depth + 1)} {op} {generated(rng, depth + 1)}"
    if roll < 0.7:
        return f"{rng.choice(UNARY)}{generated(rng, depth + 1)}"
    if roll < 0.95:
        return f"({generated(rng, depth + 1)})"
    text = generated(rng, depth + 1)
    cut = rng.randrange(len(text) + 1)
    return text[:cut] + rng.choice(JUNK) + text[cut:]  # a broken one


def test_fuzz_every_generated_expression_evaluates_or_raises_cleanly():
    rng = random.Random(2016)
    env = {"length": 2.0, "gap": 0.2, **PORTS}
    outcomes = {"value": 0, "error": 0}
    texts = {generated(rng) for _ in range(2000)}
    assert len(texts) >= 100
    for text in sorted(texts):
        try:
            value = parse(text, refs=True).evaluate(env)
        except ExprError:
            outcomes["error"] += 1
            continue
        except Exception as exc:  # noqa: BLE001 - the point of the test
            pytest.fail(f"{text!r} raised {type(exc).__name__}: {exc}")
        assert isinstance(value, bool) or (isinstance(value, float) and math.isfinite(value)), text
        outcomes["value"] += 1
    # both sides are exercised, not just one
    assert outcomes["value"] >= 50 and outcomes["error"] >= 50, outcomes


def test_fuzz_numeric_part_fields_evaluate_or_raise_cleanly():
    """The same generated texts through number(), the path a part field takes (braces, params)."""
    rng = random.Random(2017)
    outcomes = {"value": 0, "error": 0}
    texts = {generated(rng) for _ in range(2000)}
    assert len(texts) >= 100
    for text in sorted(texts):
        try:
            value = number(text, {"length": 2.0, "gap": 0.2})
        except ExprError:
            outcomes["error"] += 1
            continue
        except Exception as exc:  # noqa: BLE001 - the point of the test
            pytest.fail(f"{text!r} raised {type(exc).__name__}: {exc}")
        assert isinstance(value, float) and math.isfinite(value), text
        outcomes["value"] += 1
    assert outcomes["value"] >= 20 and outcomes["error"] >= 50, outcomes
