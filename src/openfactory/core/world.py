"""Shared simulation world: the parts currently in the scene, and deterministic randomness.

Behavior spec
-------------
Parts are boxes with an id, a type name (from the emitter's ``parts`` list), a size, and a position.
The world caps the scene at :data:`MAX_PARTS` (PLAN §5, box_emitter); :meth:`World.spawn` returns
``None`` at the cap so an emitter simply skips that spawn. Part ids are sequential (``P000001``…) so
runs are reproducible. Removed parts are counted by name for scene statistics.

Part motion is not simulated here. The headless kinematic model (``core/kinematics.py``) or the
browser physics (PLAN 1.12, mirrored back to the core) updates :attr:`Part.position`,
:attr:`Part.carrier`, :attr:`Part.vy`, and :attr:`Part.v_flow`. Parts that fall out of the scene are
counted in :attr:`World.lost` by name.

Randomness: :meth:`World.rng` gives each component its own ``random.Random`` seeded from the scene
seed and the component id as a string, so results do not depend on ``PYTHONHASHSEED`` or on the
order components draw numbers.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

MAX_PARTS = 200

Vec3 = tuple[float, float, float]


@dataclass
class Part:
    """A box in the scene. ``position`` is the box center in meters."""

    id: str
    name: str
    size: Vec3
    position: Vec3
    source: str = ""
    #: Id of the conveyor the part rests on, or None while it falls.
    carrier: str | None = None
    #: Vertical velocity in m/s (negative is down) while falling.
    vy: float = 0.0
    #: Speed in m/s along the carrier's flow axis (local +x) while carried.
    v_flow: float = 0.0

    @property
    def bottom(self) -> float:
        return self.position[1] - self.size[1] / 2

    def aabb(self) -> tuple[Vec3, Vec3]:
        """Axis-aligned bounds (min corner, max corner)."""
        half = tuple(s / 2 for s in self.size)
        lo = (self.position[0] - half[0], self.position[1] - half[1], self.position[2] - half[2])
        hi = (self.position[0] + half[0], self.position[1] + half[1], self.position[2] + half[2])
        return lo, hi


@dataclass
class World:
    seed: int = 0
    parts: dict[str, Part] = field(default_factory=dict)
    spawned: int = 0
    removed: Counter[str] = field(default_factory=Counter)
    lost: Counter[str] = field(default_factory=Counter)

    def rng(self, component_id: str) -> random.Random:
        """A deterministic random generator owned by one component."""
        return random.Random(f"{self.seed}:{component_id}")

    def spawn(self, name: str, size: Vec3, position: Vec3, source: str = "") -> Part | None:
        """Add a part, or return None if the scene already holds :data:`MAX_PARTS`."""
        if len(self.parts) >= MAX_PARTS:
            return None
        self.spawned += 1
        part = Part(f"P{self.spawned:06d}", name, size, position, source)
        self.parts[part.id] = part
        return part

    def remove(self, part_id: str) -> Part:
        """Remove a part (a part remover took it) and count it by name."""
        part = self.parts.pop(part_id)
        self.removed[part.name] += 1
        return part

    def lose(self, part_id: str) -> Part:
        """Remove a part that fell out of the scene and count it by name."""
        part = self.parts.pop(part_id)
        self.lost[part.name] += 1
        return part
