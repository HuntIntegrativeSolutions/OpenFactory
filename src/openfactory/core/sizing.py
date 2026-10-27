"""Load-time defaults sized from the scene (PLAN 2.5): dimensions left unset in a scene file that
depend on the parts the scene emits or the belt a component sits on.

Behavior spec
-------------
A component type registers a resolver with :func:`resolver`; :func:`resolve_defaults` (called by
:func:`openfactory.core.loader.build` for every component) returns the component's config with
unset dimensions filled in, or the config unchanged. Resolvers see the whole validated scene and
never change a value the scene sets. The resolved values are what the components, the kinematic
model, the viewer geometry (``/api/scene``), and ``openfactory lint`` use.

Helpers shared by the resolvers and lint: :func:`emitted_parts` (every part spec any emitter can
spawn) and :func:`belt_under` (the conveyor whose top surface a point sits on).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openfactory.core.geometry import to_local
from openfactory.core.schema import CARRIER_TYPES, Scene

#: A point is on a belt's surface within this height, meters.
ON_BELT_M = 0.01
_EPS = 1e-9

_RESOLVERS: dict[str, Callable[[Any, Scene], Any]] = {}


def resolver(
    type_name: str,
) -> Callable[[Callable[[Any, Scene], Any]], Callable[[Any, Scene], Any]]:
    """Register ``fn(config, scene) -> config`` as the load-time default resolver of a type."""

    def decorator(fn: Callable[[Any, Scene], Any]) -> Callable[[Any, Scene], Any]:
        _RESOLVERS[type_name] = fn
        return fn

    return decorator


def resolve_defaults(config: Any, scene: Scene) -> Any:
    fn = _RESOLVERS.get(config.type)
    return fn(config, scene) if fn else config


def emitted_parts(scene: Scene) -> list[Any]:
    """Every part spec any emitter in the scene can spawn."""
    return [part for c in scene.components if c.type == "box_emitter" for part in c.parts]


def tallest_part(scene: Scene) -> float | None:
    """Height of the tallest part the scene emits, or None with no emitter."""
    parts = emitted_parts(scene)
    return max(p.size[1] for p in parts) if parts else None


def belt_under(point: tuple[float, float, float], scene: Scene, reach: float = 0.0) -> Any | None:
    """The conveyor whose top surface ``point`` is on (within :data:`ON_BELT_M` in height), and
    whose footprint, widened by ``reach`` across the flow, holds it; the highest if several."""
    best = None
    for belt in scene.components:
        if belt.type not in CARRIER_TYPES or abs(belt.position[1] - point[1]) > ON_BELT_M:
            continue
        x, _, z = to_local(belt.position, belt.rotation_deg, point)
        frame_x = x + belt.length / 2 if belt.type == "turntable" else x  # deck origin: centre
        inside = -_EPS <= frame_x <= belt.length + _EPS and abs(z) <= belt.width / 2 + reach + _EPS
        if inside and (best is None or belt.position[1] > best.position[1]):
            best = belt
    return best
