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
