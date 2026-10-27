"""Stopper (PLAN §5, ``stopper``).

Behavior spec
-------------
A blade that rises out of the belt to hold parts. While ``raise`` (DO) is on, the blade rises at
``1 / raise_time_s`` of its travel per second; while it is off it lowers at the same rate, and a
command change mid-travel reverses at once (as the pusher's ``extend``). ``raised`` (DI) is true
only fully up and ``lowered`` (DI) only fully down; both are false while moving. The stopper
starts lowered (bind ``raise`` with ``initial: true`` to start it up, or ``invert: true`` for a
spring-raised stopper that lowers while energized).

The kinematic model (``core/kinematics.py``) stops a carried part whose front face reaches the
blade's upstream face while the blade is anywhere above fully lowered, and the parts behind it
queue on it (PLAN 2.0). A part whose front face is already past the blade line when the blade rises
passes over it; the next one stops. Lowering releases the queue once the blade is fully down.

Geometry: the origin is the center of the blade's upstream face at belt-top height; the blade spans
local z and stops flow along local +x. It is ``width`` across the flow (default: the width of the
belt under it), :data:`BLADE_THICKNESS_M` along it, and its raised top is ``height`` above the belt.
An unset ``height`` is sized at load (:func:`resolve_stopper`) so the raised top reaches the centre
of mass of the tallest emitted part (half its height), at least :data:`MIN_HEIGHT_M`; ``openfactory
lint`` warns (W206) when a set height is below an emitted part's centre of mass.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register
from openfactory.core.schema import Scene
from openfactory.core.sizing import belt_under, resolver, tallest_part

_EPS = 1e-9
BLADE_THICKNESS_M = 0.02
MIN_HEIGHT_M = 0.05
DEFAULT_WIDTH_M = 0.5
BLADE_REACH = 0.5  # raised top at this fraction of the tallest part's height


@resolver("stopper")
def resolve_stopper(config: Any, scene: Scene) -> Any:
    update: dict[str, float] = {}
    if config.width is None:
        belt = belt_under(config.position, scene)
        update["width"] = belt.width if belt is not None else DEFAULT_WIDTH_M
    if config.height is None:
        tallest = tallest_part(scene)
        update["height"] = max(MIN_HEIGHT_M, BLADE_REACH * tallest) if tallest else MIN_HEIGHT_M
    return config.model_copy(update=update) if update else config


@register("stopper")
class Stopper(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.position = 0.0  # fraction raised, 0 (down) .. 1 (up)

    @property
    def blocking(self) -> bool:
        """True while the blade is above fully lowered."""
        return self.position > _EPS

    def step(self, dt: float) -> None:
        rate = dt / self.config.raise_time_s
        if self.read("raise", default=False):
            self.position = min(1.0, self.position + rate)
        else:
            self.position = max(0.0, self.position - rate)
        if 1.0 - self.position <= _EPS:
            self.position = 1.0
        elif self.position <= _EPS:
            self.position = 0.0
        self.write("raised", self.position >= 1.0)
        self.write("lowered", self.position <= 0.0)

    def state(self) -> dict[str, Any]:
        return {"position": self.position, "raising": bool(self.read("raise", default=False))}

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "width": self.config.width,
            "height": self.config.height,
            "thickness": BLADE_THICKNESS_M,
        }
