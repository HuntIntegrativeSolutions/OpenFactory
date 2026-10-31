"""Safe expressions for composite components (PLAN 2.16).

Behavior spec
-------------
A composite's parts and I/O use small expressions, evaluated by this module, never by ``eval``:

- **Numeric**, in a part's fields: ``"{length} - 0.2"``. ``{name}`` is a composite parameter;
  the result must be a finite number.
- **Boolean**, in an I/O ``from:``: ``"gate.raised and not belt.running"``. ``part.port`` names a
  DI (true/false) or AI (a number, for comparisons) port of one of the composite's parts; the
  result must be true or false.

The grammar is numbers, ``true``/``false`` (or ``True``/``False``), names (parameters, or
``part.port`` references in a boolean expression), ``+ - * /``, unary ``-`` and ``+``,
parentheses, comparisons ``< <= > >= == !=`` (chains allowed), and ``and``, ``or``, ``not``. There
are no function calls, attribute access beyond ``part.port``, subscripts, strings, or powers.
Arithmetic takes numbers only, ``and``/``or``/``not`` take true/false only, and comparisons
``<``-``>=`` take numbers. The text is parsed with :func:`ast.parse` and every node is checked
against that whitelist before anything is evaluated; evaluation walks the checked tree.

Every failure raises :class:`ExprError` (a ``ValueError``) with the expression in the message:
a syntax error, a node outside the grammar, an unknown name, a type mismatch, division by zero,
a non-finite result, an expression over :data:`MAX_LENGTH` characters or nested deeper than
:data:`MAX_DEPTH`. Nothing else escapes, which ``tests/unit/test_expr.py`` checks over generated
expressions.
"""

from __future__ import annotations

import ast
import math
import operator
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass

MAX_LENGTH = 500
MAX_DEPTH = 40

Value = float | bool

_ARITH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: None}
_COMPARE = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}
_ORDERED = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_BOOL_WORDS = {"true": True, "false": False}
_BRACE = re.compile(r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")


class ExprError(ValueError):
    """An expression that does not parse, is outside the grammar, or does not evaluate."""


@dataclass(frozen=True)
class Expr:
    """A checked expression: its text, the plain names and ``part.port`` refs it uses."""

    text: str
    tree: ast.expr
    names: tuple[str, ...]
    refs: tuple[str, ...]

    def evaluate(self, env: Mapping[str, Value]) -> Value:
        """The value with ``env`` giving each name and ref; raises :class:`ExprError`."""
        try:
            value = _eval(self.tree, env, self.text)
        except ExprError:
            raise
        except (ArithmeticError, ValueError, TypeError, RecursionError) as exc:
            raise ExprError(f"expression {self.text!r}: {exc}") from None
        if isinstance(value, float) and not math.isfinite(value):
            raise ExprError(f"expression {self.text!r}: result is not a finite number")
        return value


def parse(text: str, *, refs: bool = False) -> Expr:
    """Parse and check ``text``. ``refs`` allows ``part.port`` references (boolean I/O)."""
    if not isinstance(text, str):
        raise ExprError(f"expression {text!r} is not text")
    if len(text) > MAX_LENGTH:
        raise ExprError(f"expression is {len(text)} characters; the limit is {MAX_LENGTH}")
    try:
        with warnings.catch_warnings():  # e.g. "invalid decimal literal" in "1e3x"
            warnings.simplefilter("ignore")
            tree = ast.parse(text.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        detail = exc.msg if isinstance(exc, SyntaxError) else type(exc).__name__
        raise ExprError(f"expression {text!r} does not parse: {detail}") from None
    names: list[str] = []
    found: list[str] = []
    _check(tree, text, refs, names, found, 1)
    return Expr(text, tree, tuple(dict.fromkeys(names)), tuple(dict.fromkeys(found)))


def substitute(text: str) -> str:
    """``"{length} - 0.2"`` -> ``"length - 0.2"``; a stray brace is an error."""
    plain = _BRACE.sub(r"\1", text)
    if "{" in plain or "}" in plain:
        raise ExprError(
            f"expression {text!r}: braces must hold one parameter name, like {{length}}"
        )
    return plain


def number(text: str, params: Mapping[str, float]) -> float:
    """Evaluate a numeric part field such as ``"{length} - 0.2"`` with ``params``."""
    expr = parse(substitute(text))
    unknown = [n for n in expr.names if n not in params]
    if unknown:
        known = ", ".join(sorted(params)) or "none"
        raise ExprError(f"expression {text!r}: unknown parameter {unknown[0]!r} (known: {known})")
    value = expr.evaluate(params)
    if isinstance(value, bool):
        raise ExprError(f"expression {text!r} is true/false; a number is needed here")
    return float(value)


def _check(node: ast.AST, text: str, refs: bool, names: list, found: list, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise ExprError(f"expression {text!r} is nested deeper than {MAX_DEPTH}")
    nxt = depth + 1
    if isinstance(node, ast.Constant):
        if type(node.value) not in (int, float, bool):
            raise ExprError(f"expression {text!r}: only numbers and true/false are allowed")
    elif isinstance(node, ast.Name):
        if node.id not in _BOOL_WORDS:
            names.append(node.id)
    elif isinstance(node, ast.Attribute):
        if not (refs and isinstance(node.value, ast.Name)):
            what = "a part.port reference" if refs else "'.'"
            raise ExprError(f"expression {text!r}: {what} is not allowed here")
        found.append(f"{node.value.id}.{node.attr}")
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _ARITH:
            raise ExprError(f"expression {text!r}: only + - * / are allowed")
        _check(node.left, text, refs, names, found, nxt)
        _check(node.right, text, refs, names, found, nxt)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.USub | ast.UAdd | ast.Not):
            raise ExprError(f"expression {text!r}: only unary - + and 'not' are allowed")
        _check(node.operand, text, refs, names, found, nxt)
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            _check(value, text, refs, names, found, nxt)
    elif isinstance(node, ast.Compare):
        if any(type(op) not in _COMPARE for op in node.ops):
            raise ExprError(f"expression {text!r}: only < <= > >= == != compare")
        for value in (node.left, *node.comparators):
            _check(value, text, refs, names, found, nxt)
    else:
        raise ExprError(f"expression {text!r}: {type(node).__name__} is not allowed")


def _num(value: Value, text: str, what: str) -> float:
    if isinstance(value, bool):
        raise ExprError(f"expression {text!r}: {what} takes numbers, not true/false")
    return float(value)


def _bool(value: Value, text: str, what: str) -> bool:
    if not isinstance(value, bool):
        raise ExprError(f"expression {text!r}: {what} takes true/false, not a number")
    return value


def _eval(node: ast.AST, env: Mapping[str, Value], text: str) -> Value:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, bool) else float(node.value)
    if isinstance(node, ast.Name):
        if node.id in _BOOL_WORDS:
            return _BOOL_WORDS[node.id]
        if node.id not in env:
            raise ExprError(f"expression {text!r}: unknown name {node.id!r}")
        return env[node.id]
    if isinstance(node, ast.Attribute):
        ref = f"{node.value.id}.{node.attr}"  # type: ignore[attr-defined]
        if ref not in env:
            raise ExprError(f"expression {text!r}: unknown reference {ref!r}")
        return env[ref]
    if isinstance(node, ast.BinOp):
        left = _num(_eval(node.left, env, text), text, "arithmetic")
        right = _num(_eval(node.right, env, text), text, "arithmetic")
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ExprError(f"expression {text!r}: division by zero")
            return left / right
        return _ARITH[type(node.op)](left, right)  # type: ignore[misc]
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, env, text)
        if isinstance(node.op, ast.Not):
            return not _bool(operand, text, "'not'")
        value = _num(operand, text, "unary - and +")
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BoolOp):
        what = "'and'" if isinstance(node.op, ast.And) else "'or'"
        values = [_bool(_eval(v, env, text), text, what) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env, text)
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            right = _eval(right_node, env, text)
            if isinstance(op, _ORDERED):
                _num(left, text, "< <= > >=")
                _num(right, text, "< <= > >=")
            elif isinstance(left, bool) != isinstance(right, bool):
                raise ExprError(f"expression {text!r}: == and != compare like with like")
            if not _COMPARE[type(op)](left, right):
                return False
            left = right
        return True
    raise ExprError(f"expression {text!r}: {type(node).__name__} is not allowed")
