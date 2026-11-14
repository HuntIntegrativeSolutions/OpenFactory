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

    # -- the editor's operations (PLAN 4.1a): each returns (start, end, old) of the text it
    # replaced, so a caller can undo it exactly with :meth:`splice`.

    def splice(self, start: int, end: int, text: str) -> tuple[int, int, str]:
        """Replace ``text[start:end]``; returns the span of the new text and the old text."""
        old = self.text[start:end]
        self.text = self.text[:start] + text + self.text[end:]
        return start, start + len(text), old

    def _line_start(self, i: int) -> int:
        return self.text.rfind("\n", 0, i) + 1

    def _line_end(self, i: int) -> int:
        j = self.text.find("\n", i)
        return len(self.text) if j < 0 else j

    def _leaf_end(self, node: yaml.Node) -> int:
        """Where a node's own text ends. A block collection's end mark lies at the next token,
        past any comment lines that follow it, so descend to its last leaf instead."""
        block = isinstance(node, yaml.MappingNode | yaml.SequenceNode) and not node.flow_style
        if block and node.value:
            last = node.value[-1]
            return self._leaf_end(last[1] if isinstance(last, tuple) else last)
        end = node.end_mark.index
        while end > node.start_mark.index and self.text[end - 1] in " \t\r\n":
            end -= 1  # a block scalar's mark includes its trailing newlines
        return end

    def set_node(self, path: Path_, literal: str) -> tuple[int, int, str]:
        """Replace a scalar or a flow collection (``[1, 0, 2]``, ``{tag: X}``) with ``literal``.
        A block collection is edited key by key instead."""
        _, node = self.find(path)
        if isinstance(node, yaml.MappingNode | yaml.SequenceNode) and not node.flow_style:
            raise ValueError(f"{'.'.join(map(str, path))}: a block collection; set its keys")
        return self.splice(node.start_mark.index, self._leaf_end(node), literal)

    def add_key(self, map_path: Path_, key: str, literal: str) -> tuple[int, int, str]:
        """Add ``key: literal`` as the mapping's last key: on its own line at the siblings'
        indentation for a block mapping, before the ``}`` of a flow mapping."""
        _, node = self.find(map_path)
        if not isinstance(node, yaml.MappingNode):
            raise ValueError(f"{'.'.join(map(str, map_path))}: not a mapping")
        if any(k.value == key for k, _ in node.value):
            raise ValueError(f"{'.'.join(map(str, map_path))}: {key!r} is already there")
        if node.flow_style:
            if node.value:
                at = self._leaf_end(node.value[-1][1])
                return self.splice(at, at, f", {key}: {literal}")
            at = node.start_mark.index + 1
            return self.splice(at, at, f"{key}: {literal}")
        if not node.value:
            raise ValueError(f"{'.'.join(map(str, map_path))}: an empty block mapping")
        indent = node.value[0][0].start_mark.column
        at = self._line_end(self._leaf_end(node.value[-1][1]))
        return self.splice(at, at, "\n" + " " * indent + f"{key}: {literal}")

    def delete_key(self, path: Path_) -> tuple[int, int, str]:
        """Remove a key and its value: its whole lines in a block mapping (an inline comment on
        them goes too), or ``key: value`` and one comma in a flow mapping."""
        key, value = self.find(path)
        if key is None:
            raise ValueError(f"{'.'.join(map(str, path))}: a sequence item; delete the item")
        parent = self.find(tuple(path[:-1]))[1]
        if isinstance(parent, yaml.MappingNode) and parent.flow_style:
            start, end = key.start_mark.index, self._leaf_end(value)
            after = self.text[end:]
            stripped = after.lstrip(" ")
            if stripped.startswith(","):
                end += len(after) - len(stripped) + 1
                end += len(self.text[end:]) - len(self.text[end:].lstrip(" "))
            else:
                before = self.text[:start].rstrip(" ")
                if before.endswith(","):
                    start = len(before) - 1
            return self.splice(start, end, "")
        start = self._line_start(key.start_mark.index)
        if self.text[start : key.start_mark.index].strip():
            raise ValueError(f"{'.'.join(map(str, path))}: shares its line with a list dash")
        end = self._line_end(self._leaf_end(value))
        return self.splice(start, min(end + 1, len(self.text)), "")

    def _dash(self, item: yaml.Node) -> int:
        """The index of the ``-`` that opens a block sequence item."""
        start = self._line_start(item.start_mark.index)
        dash = self.text.rfind("-", start, item.start_mark.index)
        if dash < 0:
            raise ValueError("not a block sequence item")
        return dash

    def append_item(self, seq_path: Path_, lines: list[str]) -> tuple[int, int, str]:
        """Append a block item after the sequence's last one. ``lines`` are the item's lines
        relative to its dash: the first starts ``- ``, the rest are indented by two."""
        _, node = self.find(seq_path)
        if not isinstance(node, yaml.SequenceNode) or node.flow_style or not node.value:
            raise ValueError(f"{'.'.join(map(str, seq_path))}: not a non-empty block sequence")
        last = node.value[-1]
        dash = self._dash(last)
        pad = " " * (dash - self._line_start(dash))
        at = self._line_end(self._leaf_end(last))
        return self.splice(at, at, "".join("\n" + pad + line for line in lines))

    def remove_item(self, seq_path: Path_, index: int) -> tuple[int, int, str]:
        """Remove a sequence item: :meth:`delete_item` in a block sequence, the item and one
        comma (with the spaces after it) in a flow sequence (PLAN 4.1f)."""
        _, node = self.find(seq_path)
        if not isinstance(node, yaml.SequenceNode):
            raise ValueError(f"{'.'.join(map(str, seq_path))}: not a sequence")
        if not node.flow_style:
            return self.delete_item(seq_path, index)
        if not 0 <= index < len(node.value):
            raise IndexError(index)
        item = node.value[index]
        start, end = item.start_mark.index, self._leaf_end(item)
        after = self.text[end:]
        stripped = after.lstrip(" ")
        if stripped.startswith(","):
            end += len(after) - len(stripped) + 1
            end += len(self.text[end:]) - len(self.text[end:].lstrip(" "))
        else:
            before = self.text[:start].rstrip(" ")
            if before.endswith(","):
                start = len(before) - 1
        return self.splice(start, end, "")

    def delete_item(self, seq_path: Path_, index: int) -> tuple[int, int, str]:
        """Remove a block sequence item, with the comment lines directly above it (no blank
        line between), which belong to it (ruled 2026-11-10)."""
        _, node = self.find(seq_path)
        if not isinstance(node, yaml.SequenceNode) or node.flow_style:
            raise ValueError(f"{'.'.join(map(str, seq_path))}: not a block sequence")
        if not 0 <= index < len(node.value):
            raise IndexError(index)
        item = node.value[index]
        start = self._line_start(self._dash(item))
        while start > 0:
            above = self._line_start(start - 1)
            if not self.text[above : start - 1].strip().startswith("#"):
                break
            start = above
        end = self._line_end(self._leaf_end(item))
        return self.splice(start, min(end + 1, len(self.text)), "")


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
