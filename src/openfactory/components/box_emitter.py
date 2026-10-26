"""Box emitter (PLAN §5, ``box_emitter``).

Behavior spec
-------------
While enabled, the emitter spawns one part at its origin immediately, then again every
``interval_s ± jitter_s``. Each wait is drawn uniformly from that band, and each part type is chosen
by its ``weight``, both from the emitter's own seeded generator, so a scene replays identically. The
emitter is enabled while ``enable`` (DO) is on, or always when the port is unbound. Disabling it
resets the timer, so the next enable is due at once.

A due part spawns only when its spawn volume (its box at the emitter origin) is clear of every
other part (PLAN 2.0). Until then the emitter holds that part, already drawn, so the random
sequence and therefore a replay are unchanged, and counts each tick it waits (``waiting_ticks`` in
:meth:`state`). The next interval starts from the actual spawn, so a long block never releases a
burst. A chattering enable (on, off, on within a part's fall time, as seen with OpenPLC in 1.14)
therefore can no longer stack two boxes. When the scene already holds 200 parts
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
        self.waiting_ticks = 0
        self._held = None  # part spec drawn when due, kept while the spawn volume is occupied
        self._waited = False  # the held part had to wait for its spawn volume

    def _next_interval(self) -> float:
        jitter = self.config.jitter_s
        return self.config.interval_s + (self.rng.uniform(-jitter, jitter) if jitter else 0.0)

    def _volume_clear(self, size: tuple[float, float, float]) -> bool:
        origin = self.config.position
        for other in self.world.parts.values():
            lo, hi = other.aabb()
            if all(
                lo[i] < origin[i] + size[i] / 2 - _EPS and hi[i] > origin[i] - size[i] / 2 + _EPS
                for i in range(3)
            ):
                return False
        return True

    def _try_spawn(self) -> bool:
        """Spawn the held (or a newly drawn) part if its volume is clear; False while blocked."""
        if self._held is None:
            parts = self.config.parts
            self._held = self.rng.choices(parts, weights=[p.weight for p in parts])[0]
        spec = self._held
        if not self._volume_clear(spec.size):
            self.waiting_ticks += 1
            self._waited = True
            return False
        self._held = None
        part = self.world.spawn(spec.name, spec.size, self.config.position, source=self.id)
        if part is None:
            self.skipped += 1
        else:
            self.spawned += 1
        return True

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
        if self.until_next_s <= _EPS and self._try_spawn():
            if self._waited:  # the next interval runs from this late spawn: no burst
                self.until_next_s = 0.0
                self._waited = False
            self.until_next_s += self._next_interval()

    def state(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "spawned": self.spawned,
            "skipped": self.skipped,
            "waiting_ticks": self.waiting_ticks,
        }

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "parts": [p.model_dump() for p in self.config.parts],
        }
