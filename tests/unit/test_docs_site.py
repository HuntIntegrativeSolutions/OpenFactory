"""PLAN 4.4 Done-when, the docs site: every shipped scene's guide is a page in the site's nav, every
registered component type gets a generated page, links out of docs/ go to GitHub, and the README
names the Pages URL. The build itself (`mkdocs build --strict`, no warnings) is CI step 13."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((REPO / "mkdocs.yml").read_text(encoding="utf-8"))
spec = importlib.util.spec_from_file_location("mkdocs_hooks", REPO / "tools" / "mkdocs_hooks.py")
hooks = importlib.util.module_from_spec(spec)
sys.modules["mkdocs_hooks"] = hooks
spec.loader.exec_module(hooks)


def nav_pages(entries) -> list[str]:
    pages: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            pages.append(entry)
        elif isinstance(entry, dict):
            for value in entry.values():
                pages += nav_pages(value) if isinstance(value, list) else [value]
    return pages


def test_every_shipped_scene_has_a_guide_page_in_the_nav():
    scenes = sorted(p.stem for p in (REPO / "scenes").glob("*.yaml") if ".modbus" not in p.name)
    table = (REPO / "docs" / "guides" / "README.md").read_text(encoding="utf-8")
    guides = dict(re.findall(r"^\| `([a-z-]+)` \| \[[^\]]+\]\(([^)]+\.md)\)", table, re.MULTILINE))
    assert sorted(guides) == scenes, f"guides table: {sorted(guides)}, scenes: {scenes}"
    nav = nav_pages(CONFIG["nav"])
    for scene, guide in guides.items():
        assert (REPO / "docs" / "guides" / guide).is_file(), (scene, guide)
        assert f"guides/{guide}" in nav, f"{scene}: guides/{guide} is not in mkdocs.yml's nav"


def test_every_markdown_page_in_docs_is_in_the_nav():
    nav = set(nav_pages(CONFIG["nav"]))
    excluded = [line.strip() for line in CONFIG.get("exclude_docs", "").splitlines() if line]
    pages = {
        p.relative_to(REPO / "docs").as_posix()
        for p in (REPO / "docs").rglob("*.md")
        if not any(p.relative_to(REPO / "docs").as_posix().startswith(e) for e in excluded)
    }
    assert pages <= nav, f"not in mkdocs.yml's nav: {sorted(pages - nav)}"


def test_every_registered_component_type_gets_a_page_in_the_nav():
    import openfactory.components  # noqa: F401 - registers the built-in types
    from openfactory.core.component import REGISTRY

    types = [name for name, _, _ in hooks._types()]
    assert set(types) == REGISTRY.types()
    config = hooks.on_config({"nav": yaml.safe_load(yaml.safe_dump(CONFIG["nav"]))})
    nav = nav_pages(config["nav"])
    pages = [f"{hooks.PAGES}/index.md"] + [f"{hooks.PAGES}/{name}.md" for name in types]
    assert pages == [p for p in nav if hooks.PAGES in p]
    page = hooks.type_page(*next(t for t in hooks._types() if t[0] == "pusher"))
    assert page.startswith("# `pusher`\n")
    assert "| `stroke` | number | required | > 0 |" in page  # from the scene schema
    assert "| `extend` | DO | yes |" in page  # from the registry
    assert "`stuck_on`, `stuck_off`, `jammed`, `slow`." in page
    assert "single-solenoid, spring-return linear actuator" in page  # the module docstring
    assert "[the conventions](index.md#conventions)" in page


def test_the_generated_pages_are_the_component_reference_and_components_md_is_not_a_page():
    """Ruled 2026-11-07: docs/components.md is the generator's source, not a site page; its
    introduction and conventions are the reference's index page, and links to it are rerouted."""
    assert "components.md" not in nav_pages(CONFIG["nav"])
    assert "components.md" in CONFIG["exclude_docs"].split()
    text = (REPO / "docs" / "components.md").read_text(encoding="utf-8")
    index = hooks.index_page(text)
    assert index.startswith("# Component reference\n")
    assert "## Conventions" in index and "## Conveyors" in index
    assert "- [`belt_conveyor`](belt_conveyor.md)" in index
    assert "### `" not in index  # no type section is repeated there
    # a category introduction's links are relative to docs/, rebased to the index's folder
    assert "[the flow network design](../../research/flownet-design.md)" in index
    reference = hooks.PAGES
    assert hooks.outside_link("components.md", "index.md") == f"{reference}/index.md"
    assert hooks.outside_link("../components.md#pusher", "guides/a.md") == (
        f"../{reference}/pusher.md"
    )
    assert hooks.outside_link("components.md#conventions", "index.md") == (
        f"{reference}/index.md#conventions"
    )


def test_links_out_of_docs_go_to_github_and_links_inside_are_left_alone():
    blob = hooks.BLOB
    assert hooks.outside_link("../README.md", "index.md") == f"{blob}README.md"
    assert hooks.outside_link("../../examples/pyplc/x.py#L3", "guides/a.md") == (
        f"{blob}examples/pyplc/x.py#L3"
    )
    assert hooks.outside_link("../../scenes", "guides/a.md") == f"{hooks.TREE}scenes"
    for inside in ("../scene-format.md", "openplc-quickstart.md#step-4", "#here"):
        assert hooks.outside_link(inside, "guides/a.md") is None
    assert hooks.outside_link("https://example.org/x", "index.md") is None
    md = "[a](../README.md) and\n```\n[b](../README.md)\n```\n"
    page = type("Page", (), {"file": type("File", (), {"src_uri": "index.md"})()})()
    assert hooks.on_page_markdown(md, page) == (
        f"[a]({blob}README.md) and\n```\n[b](../README.md)\n```\n"
    )


def test_the_readme_names_the_pages_url():
    assert CONFIG["site_url"] in (REPO / "README.md").read_text(encoding="utf-8")


def test_pages_cite_the_component_reference_by_link_not_by_file_name():
    """A code span naming docs/components.md is dead text on the site, where the file is not a
    page; a link to it is rerouted to the generated pages. PLAN.md's task notes keep theirs as
    history; components.md is the source itself."""
    for page in nav_pages(CONFIG["nav"]):
        if page in ("PLAN.md", "components.md") or not (REPO / "docs" / page).is_file():
            continue
        text = (REPO / "docs" / page).read_text(encoding="utf-8")
        assert "`docs/components.md`" not in text, f"{page}: link the component reference"


#: The old description of OpenFactory, replaced on 2026-11-12 by the original-work statement
#: (PLAN §9 rule 12). Spelled as a pattern so this file does not contain it.
OLD_TERM = re.compile(r"clean[-\s]?room", re.IGNORECASE)
#: An italic dated label, such as ``*Done (2026-11-06):*`` or ``*Ruled 2026-11-10:*``.
DATED = re.compile(r"\*[^*\n]*\b20\d\d-\d\d-\d\d\b[^*\n]*\*")
TASK = re.compile(r"^- \[[ x]\] ")


def _in_dated_plan_entry(lines: list[str], index: int) -> bool:
    """True when line `index` of docs/PLAN.md is inside a task's body, below a dated label of
    that task: a dated history entry."""
    for start in range(index, -1, -1):
        line = lines[start]
        if TASK.match(line):
            return bool(DATED.search("\n".join(lines[start : index + 1])))
        if start < index and line and not line[0].isspace():
            return False  # left the task body: a heading, table row or section text
    return False


def _old_term_hits(path: str, text: str) -> list[int]:
    """Line numbers of the old term in `text` that are not allowed history."""
    if path.startswith("docs/research/"):
        return []
    if path == "CHANGELOG.md":
        # generated history of commit subjects (tools/changelog.py): subjects written before
        # 2026-11-12 stay as they were (ruled 2026-11-12: never rewrite commit subjects)
        return []
    lines = text.splitlines()
    hits = []
    for match in OLD_TERM.finditer(text):
        index = text.count("\n", 0, match.start())
        if path == "docs/PLAN.md" and _in_dated_plan_entry(lines, index):
            continue
        hits.append(index + 1)
    return hits


def test_the_old_description_appears_only_in_dated_plan_history_and_research():
    """PLAN §9 rule 12 (owner, 2026-11-12): every page the public reads uses the original-work
    statement. The old term may stay only in PLAN's dated history entries, docs/research/ and
    CHANGELOG.md (generated from commit subjects)."""
    import subprocess

    files = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    found = {}
    for name in files:
        try:
            text = (REPO / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        hits = _old_term_hits(name, text)
        if hits:
            found[name] = hits
    assert not found, f"the old description of OpenFactory, outside dated history: {found}"


def test_the_old_term_check_allows_only_dated_history():
    old = "clean" + "-room"  # built from pieces so this file does not contain it
    task = f"- [x] 4.8 Release files\n      *Part 1 (2026-11-06):*\n      - a {old} checkbox\n"
    assert _old_term_hits("docs/PLAN.md", task) == []
    undated = f"- [x] 4.8 Release files\n      - a {old} checkbox\n"
    assert _old_term_hits("docs/PLAN.md", undated) == [2]
    section = "## 9. Working Agreement\n\n8. *Ruled 2026-11-10:* the " + "Clean" + " Room rule\n"
    assert _old_term_hits("docs/PLAN.md", section) == [3]
    assert _old_term_hits("README.md", "An original,\n" + "clean\nroom" + " work.") == [2]
    assert _old_term_hits("docs/research/landscape.md", f"{old} reminder") == []
    assert _old_term_hits("CHANGELOG.md", f"- docs: {old} rule lifted (`957f7ad`)") == []


#: The owner's original-work statement (2026-11-12, corrected the same day), sentence by sentence.
STATEMENT = (
    "OpenFactory is an original work.",
    "It contains no code, models, textures, scene files, documentation text, names, layouts or "
    "other assets from any commercial product.",
    "Interoperability with controllers and vendor software is built from public documentation "
    "and vendor SDK documentation.",
    "Vendor SDKs, vendor binaries and vendor-specific code are not part of this repository.",
    "Nothing derived from the internals of any commercial 3D simulator is accepted into this "
    "project",
)
PUBLIC = ("README.md", "CONTRIBUTING.md", "docs/index.md", "docs/what-openfactory-is-not.md")


def test_every_public_page_carries_the_statement_and_no_claim_of_what_was_examined():
    """PLAN §9 rule 12: the statement is a policy. The corrected sentence names no product and
    says nothing about what anyone has examined; the one it replaced over-claimed."""
    for name in PUBLIC:
        text = " ".join((REPO / name).read_text(encoding="utf-8").split())
        assert "OpenFactory is an original work" in text, name
        for sentence in STATEMENT[1:]:
            assert sentence in text, (name, sentence)
    for name in (*PUBLIC, "CLAUDE.md", ".github/pull_request_template.md"):
        text = " ".join((REPO / name).read_text(encoding="utf-8").split())
        assert "been examined" not in text, name
