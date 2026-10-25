"""Part remover (PLAN §5, ``part_remover``).

Behavior spec
-------------
Each step the remover deletes every part whose center lies inside its box volume (``size``
centered on the origin, turned by ``rotation_deg``) and counts the deleted parts by name, both for
itself and in the world totals used for scene statistics. Using the part center rather than any
overlap means a part touching the edge of the volume is not deleted until it has actually entered.
The remover has no PLC-visible ports.

Geometry: the origin is the center of the removal volume.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from openfactory.core.component import Component, register
from openfactory.core.geometry import to_local

_EPS = 1e-9


@register("part_remover")
class PartRemover(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.removed: Counter[str] = Counter()

    def contains(self, point: tuple[float, float, float]) -> bool:
        local = to_local(self.config.position, self.config.rotation_deg, point)
        return all(abs(local[i]) <= self.config.size[i] / 2 + _EPS for i in range(3))

    def step(self, dt: float) -> None:
        inside = [p.id for p in self.world.parts.values() if self.contains(p.position)]
        for part_id in inside:
            self.removed[self.world.remove(part_id).name] += 1

    def state(self) -> dict[str, Any]:
        return {"removed": sum(self.removed.values()), "by_name": dict(self.removed)}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "size": list(self.config.size)}
