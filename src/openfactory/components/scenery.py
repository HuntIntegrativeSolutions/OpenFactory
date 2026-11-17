"""Scenery (PLAN 5.2c; 5.11's type brought forward, ``scenery``).

Behavior spec
-------------
A body and nothing else: a box of ``size`` [x, y, z] metres (length, height, width) in
``color``, or, with ``mesh`` (PLAN 2.15), that mesh drawn in the box's place. It has **no I/O**
(no ports, so no tags), **no physics** and no behaviour: :meth:`Scenery.step` does nothing and
its state is empty. **Parts pass through it**: the core's kinematic model carries parts only on
conveyors, decks and grippers and never tests them against scenery, and the viewer's physics
(``web/src/physics.ts``) makes colliders only for the types it names, never scenery. It cannot be
faulted. Its use is the body of an item a drawing shows that no component covers (the
``driven_item`` and ``variable_item`` library composites, whose I/O and behaviour come from the
composite), and later a plant's surroundings: structure, platforms, panels (PLAN 5.11).

Geometry: the origin is the centre of the box; ``rotation_deg`` turns it about y as for any
component.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, num, register


@register("scenery")
class Scenery(Component):
    FAULTS = ()

    def step(self, dt: float) -> None:
        """Nothing: scenery has no behaviour and no I/O."""

    def state(self) -> dict[str, Any]:
        return {}

    @classmethod
    def describe(cls, state: dict[str, Any], geometry: dict[str, Any]) -> str:
        """One sentence for ``openfactory look`` (PLAN 5.14b; :meth:`Component.describe`)."""
        x, y, z = geometry.get("size", (1.0, 1.0, 1.0))
        return f"a {num(x)} x {num(y)} x {num(z)} m body, no I/O"

    def geometry(self) -> dict[str, Any]:
        c = self.config
        return {**super().geometry(), "size": list(c.size), "color": c.color}
