"""Variable-frequency drive and motor (PLAN 3.2, ``vfd_motor``).

Behavior spec
-------------
A drive and its motor turning one pump or one belt or roller conveyor (``drives``), in place of
that component's ``run`` and ``speed`` ports, which are left unbound: the PLC commands the drive.
While ``run`` (DO) is on and the drive is healthy, its output speed ramps to ``speed`` (AO, 0 to
100 %) at 100 % per ``accel_s``, and back to 0 when it is off; ``speed_fb`` (AI) reports the
output and ``running`` (DI) that the drive is running. The driven component sees run while the
drive runs and the drive's output as its speed command (from its next step, like any actuator).

Overload: the load on the motor (a pump's shaft power; a conveyor's is not modelled) above
``rated_kw`` for ``trip_s`` trips the drive: the ``fault`` DI latches, the output drops to 0 at
once (the motor coasts) and ``running`` goes false. A rising edge on ``reset`` clears the fault;
with ``reset`` unbound, turning ``run`` off clears it. The drive runs again on the next ``run``.

Faults: ``jammed`` (the motor stalls: it draws twice its rating, so the drive trips after
``trip_s``), ``drift`` (``speed_fb``).

Geometry: the origin is the drive cabinet.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register


@register("vfd_motor")
class VfdMotor(Component):
    FAULTS = ("jammed", "drift")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.target: Any = None
        self.output = 0.0  # %
        self.tripped = False
        self.overload_s = 0.0
        self._reset = False
        self._run = False

    def bind(self, by_id: dict[str, Any]) -> None:
        """Find the driven component (the loader calls it once every component is built)."""
        if self.target is None:
            self.target = by_id[self.config.drives]  # type: ignore[attr-defined]

    def load_kw(self) -> float:
        if self.fault is not None and self.fault.kind == "jammed":
            return 2 * self.config.rated_kw  # type: ignore[attr-defined]
        power = getattr(self.target, "power_kw", None)
        return float(power()) if power is not None and self.output > 0 else 0.0

    def step(self, dt: float) -> None:
        cfg = self.config
        run = bool(self.read("run", default=False))
        reset = bool(self.read("reset", default=False))
        if self.tripped:
            if "reset" in self.bindings:
                if reset and not self._reset:
                    self.tripped = False
            elif self._run and not run:
                self.tripped = False
        self._reset, self._run = reset, run
        active = run and not self.tripped
        command = min(max(float(self.read("speed", default=0.0)), 0.0), 100.0) if active else 0.0
        rate = 100.0 / cfg.accel_s * dt  # type: ignore[attr-defined]
        if self.output < command:
            self.output = min(command, self.output + rate)
        else:
            self.output = max(command, self.output - rate)
        if self.load_kw() > cfg.rated_kw:  # type: ignore[attr-defined]
            self.overload_s += dt
            if self.overload_s >= cfg.trip_s - 1e-9:  # type: ignore[attr-defined]
                self.tripped, self.output, active = True, 0.0, False
        else:
            self.overload_s = 0.0
        if self.target is not None:
            self.target.driven["run"] = active and self.output > 0
            self.target.driven["speed"] = self.output
        self.write("running", active)
        self.write("speed_fb", self.output)
        self.write("fault", self.tripped)

    def state(self) -> dict[str, Any]:
        return {"running": bool(self._run and not self.tripped), "speed": self.output,
                "fault": self.tripped}  # fmt: skip

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "drives": self.config.drives}  # type: ignore[attr-defined]
