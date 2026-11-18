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

**The controller's name rules** (PLAN 5.3a; the vendor's published programming manual,
publication 1756-PM004L-EN-P, revision L, November 2023, chapter 2), each checked here and
stated in our own words:

- a **tag name** has at most 40 characters, and holds no two underscores together and no
  underscore at its end (section "Guidelines for tags"); letters, digits and ``_``, starting
  with a letter or ``_``, as the shape above already requires;
- tag names are **not case sensitive** (same section), so two names that differ only in the
  case of their tag name are one tag: :func:`plc_tag_key` folds the tag name's case, and the
  scene's and the takeoff's duplicate checks compare keys;
- a **bit** is a bit of an integer tag, a SINT, INT or DINT (section "Address tag data"), and
  the widest of those is 32 bits (section "Data type"), so a bit number above 31 is refused
  here. The range for the tag's own type (0 to 7 for a SINT, 0 to 15 for an INT) needs the
  tag's data type, which a name does not carry: that is the connect-time type check of 5.3b.

**Not checked, because that manual does not state them** (listed in PLAN 5.3a): rules for a
program's name or a structure member's name (their length, their underscores, their case),
beyond the shape above. A "tag name" in these rules is the declared tag's name: ``Tag`` in
``Tag``, ``Program:P.Tag``, ``Tag.Member``, ``Tag[3]`` and ``Tag.4``. A scene's duplicates are
refused at load (:class:`openfactory.core.schema.Scene`).

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

#: A tag name's longest length (1756-PM004L-EN-P, ch. 2, "Guidelines for tags").
TAG_NAME_MAX = 40
#: The highest bit number of the widest integer tag a bit can name, a 32-bit DINT
#: (1756-PM004L-EN-P, ch. 2, "Address tag data" and "Data type").
BIT_MAX = 31

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
    tag = m["base"]
    if len(tag) > TAG_NAME_MAX:
        raise ValueError(
            f"plc_tag {name!r}: the tag name {tag!r} has {len(tag)} characters; a tag name has "
            f"at most {TAG_NAME_MAX} (1756-PM004L-EN-P, ch. 2, Guidelines for tags)"
        )
    if "__" in tag or tag.endswith("_"):
        raise ValueError(
            f"plc_tag {name!r}: the tag name {tag!r} has two underscores together or one at "
            "its end, which a tag name may not (1756-PM004L-EN-P, ch. 2, Guidelines for tags)"
        )
    if m["bit"] is not None and int(m["bit"]) > BIT_MAX:
        raise ValueError(
            f"plc_tag {name!r}: bit {int(m['bit'])} is past bit {BIT_MAX}; a bit names a bit of "
            "a SINT, INT or DINT, at most 32 bits (1756-PM004L-EN-P, ch. 2, Address tag data)"
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


def plc_tag_key(name: str) -> str:
    """The name as the controller compares it: tag names are not case sensitive
    (1756-PM004L-EN-P, ch. 2, Guidelines for tags), so the tag name's case is folded; the
    program and member names are kept as written, since that manual states no case rule for
    them. A name that does not parse is its own key."""
    m = PLC_TAG.match(name) if isinstance(name, str) else None
    if m is None:
        return name
    start, end = m.span("base")
    return name[:start] + name[start:end].casefold() + name[end:]
