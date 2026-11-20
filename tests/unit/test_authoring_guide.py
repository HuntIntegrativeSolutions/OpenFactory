"""PLAN 5.13: docs/authoring-from-drawings.md, the authoring guide for assistants. Its worked
example (an invented plant, not the sample) passes `takeoff check`; its type table gives every
type's parameters with no default, I/O ports with their kinds and line ends exactly as the code
has them; it shows the sample's sheets, says it is not safety-rated, and is in the site."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from openfactory import takeoff as tk
from openfactory.core.schema import COMPONENT_MODELS
from openfactory.core.snaps import PIPE_PORT_NAMES

REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "docs" / "authoring-from-drawings.md"
TEXT = GUIDE.read_text(encoding="utf-8")
PLACEMENT = {"id", "type", "description", "position", "io", "from", "to"}


def table() -> dict[str, tuple[set[str], dict[str, str], str]]:
    rows = {}
    for line in TEXT.splitlines():
        m = re.match(r"^\| `([a-z_]+)` \|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|$", line)
        if not m:
            continue
        kind, _, needs, ports, ends = m.groups()
        need = set(re.findall(r"`([a-z_]+)`", needs))
        port = dict(re.findall(r"`([a-z_0-9]+)` (DI|DO|AI|AO)", ports))
        rows[kind] = (need, port, ends.strip())
    return rows


def test_the_worked_example_passes_takeoff_check(tmp_path):
    block = re.search(r"```yaml\n(takeoff: .*?)```", TEXT, re.S)
    assert block, "the guide's takeoff example"
    path = tmp_path / "rinse-station.takeoff.yaml"
    path.write_text(block.group(1), encoding="utf-8")
    loaded = tk.load(path)
    errors = [str(f) for f in tk.check(loaded) if f.level == "error"]
    assert not errors, errors
    data = yaml.safe_load(block.group(1))
    sample = yaml.safe_load((REPO / "docs/samples/wash-skid/wash-skid.takeoff.yaml").read_text())
    tags = {e["tag"] for e in data["equipment"]}
    assert not tags & {e["tag"] for e in sample["equipment"]}  # not the sample's answers


def test_the_type_table_is_the_codes():
    rows = table()
    assert set(rows) == (set(COMPONENT_MODELS) - {"pipe"}) | {"unknown"}
    for kind, (need, ports, ends) in rows.items():
        if kind == "unknown":
            continue
        model = COMPONENT_MODELS[kind]
        required = {
            info.alias or name
            for name, info in model.model_fields.items()
            if info.is_required() and (info.alias or name) not in PLACEMENT
        }
        assert need == required, kind
        assert ports == tk.type_ports(kind), kind
        if kind == "tank":
            assert "`bottom`, `top`" in ends
        else:
            assert re.findall(r"`([a-z]+)`", ends) == list(PIPE_PORT_NAMES.get(kind, ())), kind


def test_it_shows_the_sample_sheets_says_it_is_not_safety_rated_and_is_in_the_site():
    assert "](samples/wash-skid/P-001.png)" in TEXT and "](samples/wash-skid/L-001.png)" in TEXT
    assert "not a safety-rated simulation" in " ".join(TEXT.split())
    nav = (REPO / "mkdocs.yml").read_text(encoding="utf-8")
    assert "- authoring-from-drawings.md" in nav
    for words in ("what to ask", "takeoff check", "takeoff scaffold", "laid out",
                  "outside this repository", "does not do"):  # fmt: skip
        assert words in " ".join(TEXT.lower().replace("*", "").split()), words
