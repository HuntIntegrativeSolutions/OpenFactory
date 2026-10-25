"""Box emitter (PLAN §5, ``box_emitter``).

Behavior spec
-------------
While enabled, the emitter spawns one part at its origin immediately, then again every
``interval_s ± jitter_s``. Each wait is drawn uniformly from that band, and each part type is chosen
by its ``weight``, both from the emitter's own seeded generator, so a scene replays identically. The
emitter is enabled while ``enable`` (DO) is on, or always when the port is unbound. Disabling it
resets the timer, so the next enable spawns at once. When the scene already holds 200 parts
(:data:`~openfactory.core.world.MAX_PARTS`) a due spawn is skipped and counted, not queued.

Geometry: the origin is the spawn point (the center of each new part).
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register

_EPS = 1e-9


@register("box_emitter")
class BoxEmitter(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rng = self.world.rng(self.id)
        self.enabled = False
        self.until_next_s = 0.0  # time left before the next spawn while enabled
        self.spawned = 0
        self.skipped = 0

    def _next_interval(self) -> float:
        jitter = self.config.jitter_s
        return self.config.interval_s + (self.rng.uniform(-jitter, jitter) if jitter else 0.0)

    def _spawn(self) -> None:
        parts = self.config.parts
        spec = self.rng.choices(parts, weights=[p.weight for p in parts])[0]
        part = self.world.spawn(spec.name, spec.size, self.config.position, source=self.id)
        if part is None:
            self.skipped += 1
        else:
            self.spawned += 1

    def step(self, dt: float) -> None:
        enabled = bool(self.read("enable", default=True))
        if not enabled:
            self.enabled = False
            return
        if not self.enabled:  # rising edge: spawn now
            self.enabled = True
            self.until_next_s = 0.0
        else:
            self.until_next_s -= dt
        if self.until_next_s <= _EPS:
            self._spawn()
            self.until_next_s += self._next_interval()

    def state(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "spawned": self.spawned, "skipped": self.skipped}

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "parts": [p.model_dump() for p in self.config.parts],
        }
