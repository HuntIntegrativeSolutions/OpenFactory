"""Component base class and type registry (PLAN §3.1, task 1.3).

Behavior spec
-------------
A component is one piece of plant equipment declared in the scene. It is built from its validated
scene model (``config``), the shared :class:`~openfactory.core.iotable.IOTable`, and the
:class:`~openfactory.core.world.World`. Its ports come from the scene schema's ``io`` model:

- ``inputs`` are the bound DO/AO tags the PLC writes and the component reads;
- ``outputs`` are the bound DI/AI tags the component writes and the PLC reads.

Components read inputs with :meth:`Component.read` and write outputs with :meth:`Component.write`.
An unbound optional port reads as the caller's default, and writes to it are dropped, so behavior
code never branches on whether a port is wired. Every write is also kept in :attr:`Component.raw`.
An output port whose binding carries a signal transform (PLAN 2.17: invert, delays, combine,
window) is not written to its tag here: :class:`~openfactory.core.transforms.TransformStage` writes
the conditioned value after all components have stepped. An input port bound with ``invert: true``
reads the opposite of what the PLC wrote. :meth:`Component.step` advances the component by
``dt`` seconds and must be deterministic: no clock reads, randomness only from ``world.rng``.

Concrete types register themselves with ``@register("belt_conveyor")``. Registering a name twice
raises, so two modules cannot silently claim the same type.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

from openfactory.core.iotable import IOTable, Tag, Value, Writer
from openfactory.core.schema import (
    PLC_WRITES,
    ComponentBase,
    PortSpec,
    binding_tag,
    binding_transform,
    port_specs,
)
from openfactory.core.world import World


class Component(ABC):
    """Base class for every simulated component type."""

    #: Scene ``type`` string; set by :func:`register`.
    type_name: ClassVar[str] = ""

    def __init__(self, config: ComponentBase, io: IOTable, world: World) -> None:
        self.config = config
        self.id: str = config.id
        self.io = io
        self.world = world
        io_model = config.io  # type: ignore[attr-defined]
        self.ports: dict[str, PortSpec] = port_specs(type(io_model))
        #: Port name → bound tag name, for bound ports only.
        self.bindings: dict[str, str] = {
            name: binding_tag(spec.value(io_model))
            for name, spec in self.ports.items()
            if spec.value(io_model) is not None
        }
        transforms = {
            name: binding_transform(self.ports[name].value(io_model)) for name in self.bindings
        }
        #: Output ports whose tag the transform stage writes instead of this component.
        self.conditioned: set[str] = {
            name for name, t in transforms.items() if t and self.ports[name].kind not in PLC_WRITES
        }
        #: Input ports bound NC (``invert: true``).
        self.inverted: set[str] = {
            name for name, t in transforms.items() if t.invert and self.ports[name].kind == "DO"
        }
        #: Last value written to each output port, before any transform.
        self.raw: dict[str, Value] = {}

    @property
    def type(self) -> str:
        return self.type_name

    @property
    def inputs(self) -> dict[str, Tag]:
        """Bound ports written by the PLC (DO/AO), as current tag snapshots."""
        return self._tags(lambda spec: spec.kind in PLC_WRITES)

    @property
    def outputs(self) -> dict[str, Tag]:
        """Bound ports written by this component (DI/AI), as current tag snapshots."""
        return self._tags(lambda spec: spec.kind not in PLC_WRITES)

    def _tags(self, keep: Callable[[PortSpec], bool]) -> dict[str, Tag]:
        return {
            port: self.io.tag(tag) for port, tag in self.bindings.items() if keep(self.ports[port])
        }

    def read(self, port: str, default: Value) -> Value:
        """Current value of an input port, or ``default`` when the port is unbound."""
        self._check_port(port, plc_writes=True)
        tag = self.bindings.get(port)
        if tag is None:
            return default
        value = self.io.get(tag)
        return (not value) if port in self.inverted else value

    def write(self, port: str, value: Value) -> None:
        """Set an output port. A write to an unbound port is dropped."""
        self._check_port(port, plc_writes=False)
        self.raw[port] = value
        tag = self.bindings.get(port)
        if tag is not None and port not in self.conditioned:
            self.io.set(tag, value, Writer.SIM)

    def _check_port(self, port: str, *, plc_writes: bool) -> None:
        spec = self.ports.get(port)
        if spec is None:
            raise KeyError(f"{self.id}: {self.type_name} has no port {port!r}")
        if (spec.kind in PLC_WRITES) != plc_writes:
            verb = "read" if plc_writes else "write"
            raise ValueError(f"{self.id}: cannot {verb} {spec.kind} port {port!r}")

    @abstractmethod
    def step(self, dt: float) -> None:
        """Advance by ``dt`` seconds. Deterministic: never read the clock."""

    def state(self) -> dict[str, Any]:
        """Dynamic state the viewer needs each frame (PLAN §3.3)."""
        return {}

    def geometry(self) -> dict[str, Any]:
        """Static geometry sent to the viewer once. Subclasses add their dimensions."""
        return {
            "id": self.id,
            "type": self.type_name,
            "position": list(self.config.position),
            "rotation_deg": self.config.rotation_deg,
        }


C = TypeVar("C", bound=type[Component])


class ComponentRegistry:
    """Maps scene ``type`` strings to component classes."""

    def __init__(self) -> None:
        self._classes: dict[str, type[Component]] = {}

    def register(self, type_name: str) -> Callable[[C], C]:
        def decorator(cls: C) -> C:
            if type_name in self._classes:
                existing = self._classes[type_name]
                raise ValueError(
                    f"component type {type_name!r} already registered by "
                    f"{existing.__module__}.{existing.__qualname__}"
                )
            if not (isinstance(cls, type) and issubclass(cls, Component)):
                raise TypeError(f"{cls!r} is not a Component subclass")
            cls.type_name = type_name
            self._classes[type_name] = cls
            return cls

        return decorator

    def resolve(self, type_name: str) -> type[Component]:
        try:
            return self._classes[type_name]
        except KeyError:
            known = ", ".join(sorted(self._classes)) or "none"
            raise KeyError(f"unknown component type {type_name!r} (registered: {known})") from None

    def types(self) -> set[str]:
        return set(self._classes)

    def __contains__(self, type_name: object) -> bool:
        return type_name in self._classes


#: The default registry used by ``@register`` and the scene loader.
REGISTRY = ComponentRegistry()
register = REGISTRY.register
