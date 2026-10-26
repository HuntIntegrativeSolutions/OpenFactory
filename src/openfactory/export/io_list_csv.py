"""I/O list CSV export (PLAN task 1.8).

Behavior spec
-------------
:func:`io_list_csv` turns a scene into the HIS Controls I/O list format consumed by
``/io-validate``, ``/io-to-l5x``, and ``/io-to-ignition-tags``. One row per bound tag, in Modbus
register-map order (kind, then address), with these columns:

- ``tag``: tag name (``AREA_EQUIP_NNN_SUFFIX``).
- ``io_type``: DI, DO, AI, or AO.
- ``dtype``: engineering data type, ``bool`` or ``float32``.
- ``description``: ``"<component description> — <port description>"``.
- ``component``: owning component id.
- ``address``: physical PLC I/O address. Blank, because a simulated point has none; the engineer
  fills it when the I/O list is reused for a real panel.
- ``modbus_address``: zero-based Modbus address from the same register map the server uses
  (:func:`~openfactory.protocols.modbus.scene_register_map`: pinned, then lockfile, then
  allocated; ADR-0004), for example ``CO:0``, ``DI:3``, ``HR:0-1`` (DI discrete inputs, CO coils,
  IR input registers, HR holding registers). Exporting from a scene file reads the lockfile for
  the export's analog encoding, ``<scene>.<encoding>.modbus.yaml``, and creates or updates it,
  because the CSV is what a PLC program is written against.
- ``modbus_encoding``: ``bit``, ``float32 high word first``, or ``int16_scaled 0-32767``
  (ADR-0003).
- ``units``, ``range_min``, ``range_max``: engineering units and the values at raw 0 and raw 32767
  (analog tags only).
- ``transform``: the binding's signal conditioning (PLAN 2.17), for example
  ``or(PE_01.detect, PE_02.detect); NC (inverted); on-delay 0.2 s``; empty when there is none.

PLAN 1.8 lists ``kind`` and no ``address`` column; ``/io-validate`` requires ``io_type`` and
``address``, so those two names follow the validator (see the note under task 1.8).
"""

from __future__ import annotations

import csv
import io as _io
import sys
from pathlib import Path

from openfactory.core.loader import build_iotable
from openfactory.core.schema import Scene, load_scene
from openfactory.protocols.modbus import Encoding, scene_register_map

COLUMNS = (
    "tag",
    "io_type",
    "dtype",
    "description",
    "component",
    "address",
    "modbus_address",
    "modbus_encoding",
    "units",
    "range_min",
    "range_max",
    "transform",
)

ENCODING_LABEL = {"float32": "float32 high word first", "int16_scaled": "int16_scaled 0-32767"}


def _number(value: float) -> str:
    return f"{value:g}"


def io_list_rows(
    scene: Scene,
    encoding: Encoding | None = None,
    *,
    scene_path: str | Path | None = None,
    write_lock: bool = True,
) -> list[dict[str, str]]:
    """Rows for :func:`io_list_csv`. ``encoding`` defaults to the scene's analog encoding.

    With ``scene_path``, addresses honor the scene's lockfile (ADR-0004) and, with ``write_lock``,
    create or update it.
    """
    enc: Encoding = encoding or scene.plc.analog_encoding
    table = build_iotable(scene)
    rows = []
    register_map = scene_register_map(scene, enc, scene_path=scene_path, write=write_lock)
    transforms = {b.name: b.transform.describe() for b in scene.tag_bindings()}
    for a in register_map.assignments:
        tag = table.tag(a.tag)
        analog = tag.kind in ("AI", "AO")
        lo, hi = tag.range or (None, None)
        rows.append(
            {
                "tag": tag.name,
                "io_type": tag.kind,
                "dtype": tag.dtype,
                "description": tag.description,
                "component": tag.owner,
                "address": "",
                "modbus_address": a.label,
                "modbus_encoding": ENCODING_LABEL[enc] if analog else "bit",
                "units": (tag.units or "") if analog else "",
                "range_min": _number(lo) if analog and lo is not None else "",
                "range_max": _number(hi) if analog and hi is not None else "",
                "transform": transforms.get(tag.name, ""),
            }
        )
    return rows


def io_list_csv(
    scene: Scene,
    encoding: Encoding | None = None,
    *,
    scene_path: str | Path | None = None,
    write_lock: bool = True,
) -> str:
    """The I/O list as CSV text (UTF-8, ``\\n`` line endings, header row first)."""
    buf = _io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(io_list_rows(scene, encoding, scene_path=scene_path, write_lock=write_lock))
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    """``python -m openfactory.export.io_list_csv SCENE [OUT.csv]`` (stdout without OUT)."""
    args = sys.argv[1:] if argv is None else argv
    if not args or len(args) > 2:
        print(
            "usage: python -m openfactory.export.io_list_csv SCENE.yaml [OUT.csv]", file=sys.stderr
        )
        return 2
    text = io_list_csv(load_scene(args[0]), scene_path=args[0])
    if len(args) == 2:
        Path(args[1]).write_text(text, encoding="utf-8")
        print(f"wrote {args[1]}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
