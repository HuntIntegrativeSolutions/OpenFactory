"""Scene format migration (PLAN 4.7): ``openfactory migrate``.

Behavior spec
-------------
A scene, library composite or example names its format on its ``openfactory:`` line. The format
changes only on a breaking change (docs/versioning.md); each such change adds one
:class:`Migration` to :data:`MIGRATIONS`, keyed by the version it migrates *from*.
:func:`migrate_text` reads the version and applies migrations in turn until the text is at
:data:`~openfactory.core.schema.SCHEMA_VERSION`, rewriting the ``openfactory:`` value after each
step. A file already at the current version is returned **byte for byte unchanged** (the 0.1
path today: the format has never broken, so :data:`MIGRATIONS` is empty). A version with no
migration, a newer version than this package knows, or no version line is an error.

Rewrites keep the file's comments, key order, quoting and layout: a migration edits a
:class:`Document`, which finds nodes with PyYAML's composer (each node knows where it starts and
ends in the text) and splices new text into exactly those spans, leaving every other character
alone. There is no load-and-dump round trip, which would drop comments. ``tests/unit/
test_migrate.py`` proves it with a synthetic migration that exists only in the test.

The corpus (PLAN 4.7): for every version a migration starts from, ``tests/migrate/<version>/``
holds every shipped scene as it was at that version, and migrating each must give the shipped
file exactly; a test fails if a migration is added without its corpus.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import yaml

from openfactory.core.schema import SCHEMA_VERSION

Path_ = tuple[str | int, ...]


class Document:
    """YAML text edited by position: each edit replaces one node's (or key's) span of text."""

    def __init__(self, text: str) -> None:
        self.text = text

    def _root(self) -> yaml.Node:
        node = yaml.compose(self.text, Loader=yaml.SafeLoader)
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("not a YAML mapping at the top level")
        return node

    def find(self, path: Path_) -> tuple[yaml.Node | None, yaml.Node]:
        """(key node, value node) at ``path``: mapping keys as str, sequence items as int. The
        key node is None for a sequence item. KeyError if the path is not there."""
        key: yaml.Node | None = None
        node = self._root()
        for step in path:
            if isinstance(step, int):
                if not isinstance(node, yaml.SequenceNode) or not 0 <= step < len(node.value):
                    raise KeyError(path)
                key, node = None, node.value[step]
                continue
            if not isinstance(node, yaml.MappingNode):
                raise KeyError(path)
            match = [(k, v) for k, v in node.value if k.value == step]
            if not match:
                raise KeyError(path)
            key, node = match[0]
        return key, node

    def has(self, path: Path_) -> bool:
        try:
            self.find(path)
        except KeyError:
            return False
        return True

    def value(self, path: Path_) -> str:
        """The scalar value at ``path``, as YAML read it."""
        _, node = self.find(path)
        if not isinstance(node, yaml.ScalarNode):
            raise ValueError(f"{'.'.join(map(str, path))}: not a scalar")
        return node.value

    def _splice(self, node: yaml.Node, text: str) -> None:
        start, end = node.start_mark.index, node.end_mark.index
        self.text = self.text[:start] + text + self.text[end:]

    def set_scalar(self, path: Path_, literal: str) -> None:
        """Replace the scalar at ``path`` with ``literal``, YAML source text such as ``"0.2"``
        (quotes included) or ``2.5``."""
        _, node = self.find(path)
        if not isinstance(node, yaml.ScalarNode):
            raise ValueError(f"{'.'.join(map(str, path))}: not a scalar")
        self._splice(node, literal)

    def rename_key(self, path: Path_, new: str) -> None:
        """Rename the mapping key at ``path`` (its value, comments and position stay)."""
        key, _ = self.find(path)
        if key is None:
            raise ValueError(f"{'.'.join(map(str, path))}: a sequence item has no key")
        self._splice(key, new)


@dataclass(frozen=True)
class Migration:
    """One breaking format change: rewrites a ``source`` document into ``target``."""

    source: str
    target: str
    summary: str
    apply: Callable[[Document], None] = field(repr=False)


#: Keyed by the version each migrates from. Empty: format 0.1 has never broken (PLAN 4.7, ruled
#: 2026-11-06); the first breaking change adds its migration here, with its corpus.
MIGRATIONS: dict[str, Migration] = {}


@dataclass(frozen=True)
class Result:
    text: str
    source: str  # the version the text was at
    steps: tuple[str, ...]  # each applied migration's summary, in order

    @property
    def changed(self) -> bool:
        return bool(self.steps)


def _version_key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return ()


def migrate_text(
    text: str,
    *,
    migrations: dict[str, Migration] | None = None,
    current: str = SCHEMA_VERSION,
) -> Result:
    """The text at the ``current`` format. ValueError for no version line, an unknown or newer
    version, or a chain that does not reach ``current``."""
    table = MIGRATIONS if migrations is None else migrations
    doc = Document(text)
    try:
        version = doc.value(("openfactory",))
    except KeyError:
        raise ValueError(
            'no `openfactory:` version line (a scene starts openfactory: "0.1")'
        ) from None
    source, steps = version, []
    while version != current:
        step = table.get(version)
        if step is None:
            newer = _version_key(version) > _version_key(current) and bool(_version_key(version))
            if newer:
                raise ValueError(
                    f"format {version} is newer than this OpenFactory's {current}: upgrade it"
                )
            known = ", ".join(sorted(table)) or "none"
            raise ValueError(f"no migration from format {version} (migrations from: {known})")
        if len(steps) > len(table):
            raise ValueError(f"migrations from {source} do not reach {current}")
        step.apply(doc)
        doc.set_scalar(("openfactory",), f'"{step.target}"')
        steps.append(f"{step.source} -> {step.target}: {step.summary}")
        version = step.target
    return Result(doc.text if steps else text, source, tuple(steps))
