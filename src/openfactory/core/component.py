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

Fault injection (PLAN 2.6)
--------------------------
Every component has :meth:`Component.fault_inject` and :meth:`Component.clear_fault`; a type lists
the kinds that make sense for it in :attr:`Component.FAULTS` (an empty tuple: none), and asking for
another kind raises ``ValueError`` naming the supported ones. A component has at most one fault;
injecting replaces the current one. The kinds (:data:`FAULT_KINDS`) and their parameters:

- ``stuck_on`` / ``stuck_off``: a DI output reads true / false whatever the device senses
  (``port``: one DI port; default every DI port). This is the device's output, before any 2.17
  transform, so an NC (``invert: true``) eye stuck on reads false at the PLC. Not for AI ports.
- ``jammed``: the belt or actuator stops moving where it is; its feedback stays honest (a belt's
  ``running`` still follows ``run``; limit switches report the true position).
- ``slow``: the belt or actuator moves at ``factor`` of its speed (default 0.5).
- ``misaligned``: the sensor sees ``offset_m`` late (positive) or early: a photo eye's beam is
  shifted that far along the flow, a distance sensor reads that much more.
- ``noisy``: random dropouts, on average ``rate`` per second (default 0.5), each ``duration_s`` long
  (default 0.2 s, long enough for 100 ms PLC polling to see): a DI reads false, an AI reads the top
  of its range (no echo). Drawn from the component's own fault RNG, so a replay is unchanged and
  the scene's other random draws are not shifted.
- ``drift``: an AI reads ``rate`` units per second (default 0.01) more for every second since the
  fault was injected, in simulated time.

Faults on DI/AI outputs are applied in :meth:`Component.write`, before the raw value is kept;
``jammed`` and ``slow`` reach the type's motion through :attr:`Component.speed_factor`. The fault's
clock runs in :meth:`Component.tick`, which :meth:`openfactory.core.loader.LoadedScene.step` calls
(it then calls :meth:`Component.step`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from openfactory.core.iotable import IOTable, Tag, Value, Writer
from openfactory.core.schema import (
    PLC_WRITES,
    ComponentBase,
    PortSpec,
    binding_tag,
    binding_transform,
    instance_ports,
)
from openfactory.core.world import World

FAULT_KINDS = ("stuck_on", "stuck_off", "jammed", "slow", "misaligned", "noisy", "drift")
#: Parameters each fault kind accepts, with their defaults.
FAULT_PARAMS: dict[str, dict[str, Any]] = {
    "stuck_on": {"port": None},
    "stuck_off": {"port": None},
    "jammed": {},
    "slow": {"factor": 0.5},
    "misaligned": {"offset_m": 0.05},
    "noisy": {"rate": 0.5, "duration_s": 0.2},
    "drift": {"rate": 0.01},
}


@dataclass
class Fault:
    """An injected fault (PLAN 2.6): its kind, parameters, and simulated time since injection."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    #: Ticks completed when it was injected; it acts from the next tick.
    tick: int = 0
    elapsed_s: float = 0.0
    #: Seconds left in the current dropout (``noisy``).
    dropout_s: float = 0.0

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": dict(self.params), "tick": self.tick}


class Component(ABC):
    """Base class for every simulated component type."""

    #: Scene ``type`` string; set by :func:`register`.
    type_name: ClassVar[str] = ""
    #: Fault kinds this type supports (PLAN 2.6).
    FAULTS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, config: ComponentBase, io: IOTable, world: World) -> None:
        self.config = config
        self.id: str = config.id
        self.io = io
        self.world = world
        io_model = config.io  # type: ignore[attr-defined]
        self.ports: dict[str, PortSpec] = instance_ports(config)
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
        #: The injected fault, if any (PLAN 2.6).
        self.fault: Fault | None = None

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
        """Set an output port. A write to an unbound port is dropped. An injected fault on the
        port's signal is applied first (PLAN 2.6)."""
        self._check_port(port, plc_writes=False)
        if self.fault is not None:
            value = self._faulted(port, value)
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

    def tick(self, dt: float) -> None:
        """One tick: advance the fault's clock (and a ``noisy`` fault's dropouts), then step."""
        fault = self.fault
        if fault is not None:
            fault.elapsed_s += dt
            if fault.kind == "noisy":
                if fault.dropout_s > 0:
                    left = fault.dropout_s - dt
                    fault.dropout_s = left if left > 1e-9 else 0.0  # n·dt is n ticks, not n + 1
                elif self._fault_rng.random() < fault.params["rate"] * dt:
                    fault.dropout_s = fault.params["duration_s"]
        self.step(dt)

    # -- faults (PLAN 2.6) -------------------------------------------------------------------

    @property
    def speed_factor(self) -> float:
        """1 normally, 0 while ``jammed``, ``factor`` while ``slow``: multiply every motion rate."""
        if self.fault is None:
            return 1.0
        if self.fault.kind == "jammed":
            return 0.0
        if self.fault.kind == "slow":
            return float(self.fault.params["factor"])
        return 1.0

    def fault_inject(self, kind: str, **params: Any) -> Fault | None:
        """Inject a fault, replacing any current one; returns the one it replaced."""
        if kind not in self.FAULTS:
            supported = ", ".join(self.FAULTS) or "none"
            raise ValueError(
                f"{self.id}: a {self.type_name} has no {kind!r} fault (supports: {supported})"
            )
        allowed = FAULT_PARAMS[kind]
        unknown = sorted(set(params) - set(allowed))
        if unknown:
            raise ValueError(
                f"{self.id}: {kind} takes {', '.join(allowed) or 'no parameters'}, "
                f"not {', '.join(unknown)}"
            )
        values = {**allowed, **params}
        self._check_fault(kind, values)
        replaced, self.fault = self.fault, Fault(kind, values, tick=self.world.tick)
        if kind == "noisy":
            self._fault_rng = self.world.rng(f"{self.id}:fault")
        return replaced

    def clear_fault(self) -> Fault | None:
        """Remove the fault; returns it (None if there was none)."""
        cleared, self.fault = self.fault, None
        return cleared

    def _check_fault(self, kind: str, values: dict[str, Any]) -> None:
        di_ports = [n for n, p in self.ports.items() if p.kind == "DI"]
        if kind in ("stuck_on", "stuck_off"):
            port = values["port"]
            if port is not None and port not in di_ports:
                raise ValueError(
                    f"{self.id}: {kind} needs a DI port ({', '.join(di_ports) or 'none'}), "
                    f"not {port!r}"
                )
        elif kind == "slow" and not 0 < float(values["factor"]) < 1:
            raise ValueError(
                f"{self.id}: slow factor must be between 0 and 1, not {values['factor']}"
            )
        elif kind == "noisy" and (float(values["rate"]) <= 0 or float(values["duration_s"]) <= 0):
            raise ValueError(f"{self.id}: noisy rate and duration_s must be positive")
        for key, value in values.items():
            if key != "port" and not isinstance(value, int | float):
                raise ValueError(f"{self.id}: {kind} {key} must be a number, not {value!r}")

    def _faulted(self, port: str, value: Value) -> Value:
        fault = self.fault
        assert fault is not None
        kind = self.ports[port].kind
        if fault.kind in ("stuck_on", "stuck_off") and kind == "DI":
            if fault.params["port"] in (None, port):
                return fault.kind == "stuck_on"
        elif fault.kind == "noisy" and fault.dropout_s > 0:
            if kind == "DI":
                return False
            if kind == "AI" and self.ports[port].range:
                return self.ports[port].range[1]  # type: ignore[index]
        elif fault.kind == "drift" and kind == "AI":
            return float(value) + float(fault.params["rate"]) * fault.elapsed_s
        elif fault.kind == "misaligned" and kind == "AI":
            return float(value) + float(fault.params["offset_m"])
        return value

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
