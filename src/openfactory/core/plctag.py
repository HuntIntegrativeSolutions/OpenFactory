"""The plant's tag names (PLAN 5.3a): the ``plc_tag`` grammar and its forms.

Behavior spec
-------------
A binding's ``plc_tag`` names the point in the plant's own controller program. The scene's
``tag`` stays the key for everything inside OpenFactory (the I/O table, scripts, checks, viewer,
Modbus lockfiles, OPC UA nodes); ``plc_tag`` is used only where OpenFactory faces the plant's
controller and documents: the I/O list CSV, ``CONTEXT.md``, the L5X stub, the SCADA export with
a device path, and (5.3b) the Logix path.

:func:`parse_plc_tag` checks a name's **shape** and says which of five forms it is:

- ``controller``: a base name, ``LT101_PV``;
- ``program``: ``Program:<program>.<name>``, ``Program:Transfer.P101_Run``;
- ``member``: a structure member, ``CV201.Running`` (or ``Program:X.Tag.Member``);
- ``element``: an array element with no member after it, ``Word[3]`` or ``Grid[1,2]``;
- ``bit``: a final segment of digits, ``Line2_DI_Word.4``.

The shape is the one pycomm3 1.2.16 splits a tag request on (``LogixDriver._parse_tag_request``,
https://github.com/ottowayi/pycomm3, ``pycomm3/logix_driver.py``): a base that starts with
``Program:`` takes the next segment with it; segments are split on ``.``; ``[i]``, ``[i,j]`` or
``[i,j,k]`` index a segment; a last segment made only of digits is a bit. Each name in it is a
letter or ``_`` followed by letters, digits or ``_``.

**Not checked yet** (PLAN 5.3a, blocker of 2026-11-16): the controller's own name rules, which
are to come from the vendor's published programming manual. Until they are read and cited, this
module checks no length limit, no rule on leading, doubled or trailing underscores, no bit
range per data type, and no duplicate that differs only in case. A scene's exact duplicates are
refused at load (:class:`openfactory.core.schema.Scene`), which is OpenFactory's own rule, not
the controller's.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_INDEX = r"\[\d+(?:,\d+){0,2}\]"
PLC_TAG = re.compile(
    rf"^(?:Program:(?P<program>{_IDENT})\.)?"
    rf"(?P<base>{_IDENT})(?P<index>{_INDEX})?"
    rf"(?P<rest>(?:\.{_IDENT}(?:{_INDEX})?)*)"
    rf"(?:\.(?P<bit>\d+))?$"
)

#: The forms a plc_tag can take, in the order the L5X comment lists them.
FORMS = ("controller", "program", "member", "element", "bit")
#: Forms the L5X stub declares; the others are parts of a tag the plant's program declares.
DECLARED = frozenset({"controller", "program"})


@dataclass(frozen=True)
class PlcTag:
    """A parsed ``plc_tag``: the name as written, its form, and its program when scoped."""

    name: str
    form: str
    program: str | None
    #: The declared tag it is part of: ``Program:X.Tag`` or ``Tag``, without member, index or bit.
    base: str


def parse_plc_tag(name: str) -> PlcTag:
    """The name's form; ValueError naming what is wrong with a name of the wrong shape."""
    if not isinstance(name, str) or not name:
        raise ValueError("plc_tag must be a non-empty string")
    if name != name.strip():
        raise ValueError(f"plc_tag {name!r} has leading or trailing spaces")
    m = PLC_TAG.match(name)
    if m is None:
        raise ValueError(
            f"plc_tag {name!r} is not a controller tag reference: write NAME, "
            "Program:PROGRAM.NAME, NAME.MEMBER, NAME[i] or NAME.BIT, each name a letter or _ "
            "then letters, digits or _"
        )
    program = m["program"]
    base = f"Program:{program}.{m['base']}" if program else m["base"]
    if m["bit"] is not None:
        form = "bit"
    elif m["rest"]:
        form = "member"
    elif m["index"]:
        form = "element"
    else:
        form = "program" if program else "controller"
    return PlcTag(name, form, program, base)
