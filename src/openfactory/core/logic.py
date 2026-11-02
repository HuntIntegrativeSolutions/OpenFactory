"""Composite logic stage (PLAN 2.5b): part ports driven by a composite's ``logic:`` block.

Behavior spec
-------------
Each :class:`~openfactory.core.composite.LogicDrive` names a built-in part's DO port and a boolean
expression over parts' DI/AI ports and the composite's ``command`` tags. :meth:`LogicStage.step`
runs every tick after the kinematic model and before the components step: it evaluates each
expression with every referenced part port's last written value (``Component.raw``: the device's
own output, faults included, before any 2.17 transform; a port not yet written reads false, or 0
for an AI) and each command's tag as the PLC last wrote it, and sets the part's
:attr:`Component.driven` value for that port, which :meth:`Component.read` returns instead of a
tag. A part therefore acts on values from the end of the previous tick: one tick of delay.
:meth:`prime` evaluates once at load, so the first tick already has a value. An expression that
fails at run time (division by zero) drives false.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openfactory.core.expr import Expr, ExprError, parse

if TYPE_CHECKING:
    from openfactory.core.component import Component
    from openfactory.core.composite import LogicDrive
    from openfactory.core.iotable import IOTable


@dataclass
class _Drive:
    part: Component
    port: str
    expr: Expr
    refs: list[tuple[str, Component, str, bool]]  # ref text, component, port, is analog
    commands: dict[str, str]  # name -> tag


class LogicStage:
    def __init__(self, io: IOTable, drives: list[_Drive]) -> None:
        self.io = io
        self.drives = drives

    @classmethod
    def build(
        cls, io: IOTable, components: list[Component], drives: list[LogicDrive]
    ) -> LogicStage:
        by_id = {c.id: c for c in components}
        out = []
        for d in drives:
            expr = parse(d.text, refs=True)
            refs = []
            for ref in expr.refs:
                cid, port = ref.split(".")
                comp = by_id[cid]
                refs.append((ref, comp, port, comp.ports[port].kind == "AI"))
            out.append(_Drive(by_id[d.part_id], d.port, expr, refs, dict(d.commands)))
        return cls(io, out)

    def step(self) -> None:
        for d in self.drives:
            env: dict[str, Any] = {
                ref: comp.raw.get(port, 0.0 if analog else False)
                for ref, comp, port, analog in d.refs
            }
            for name, tag in d.commands.items():
                env[name] = bool(self.io.get(tag))
            try:
                value = bool(d.expr.evaluate(env))
            except ExprError:
                value = False
            d.part.driven[d.port] = value

    prime = step
