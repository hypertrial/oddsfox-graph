"""Structure and branding contracts for the OddsFox Graph docs site."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"


def _nav_targets(items):
    for item in items:
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for value in item.values():
                if isinstance(value, str):
                    yield value
                else:
                    yield from _nav_targets(value)


def _config():
    text = (REPO_ROOT / "mkdocs.yml").read_text()
    text = re.sub(r"!!python/name:([^\s]+)", r"\1", text)
    return yaml.safe_load(text)


def test_navigation_contains_every_docs_page():
    targets = set(_nav_targets(_config()["nav"]))
    pages = {path.relative_to(DOCS_DIR).as_posix() for path in DOCS_DIR.rglob("*.md")}

    assert targets == pages
    for target in targets:
        assert (DOCS_DIR / target).is_file(), target


def test_material_theme_uses_native_navigation_and_dark_palette():
    config = _config()
    features = set(config["theme"]["features"])
    extension_names = {
        item if isinstance(item, str) else next(iter(item))
        for item in config["markdown_extensions"]
    }

    assert config["theme"]["name"] == "material"
    assert config["site_name"] == "OddsFox Graph"
    assert config["site_url"] == "https://graph.oddsfox.io/"
    assert config["repo_url"] == "https://github.com/hypertrial/oddsfox-graph"
    assert config["repo_name"] == "hypertrial/oddsfox-graph"
    assert config["theme"]["logo"] == "assets/images/oddsfox-favicon.png"
    assert config["theme"]["favicon"] == "assets/images/oddsfox-favicon.png"
    assert config["theme"]["font"] is False
    assert config["extra_css"] == ["assets/stylesheets/extra.css"]
    assert config["extra_javascript"] == [
        "assets/javascripts/mermaid.min.js",
        "assets/javascripts/mermaid.js",
    ]
    assert "navigation.expand" not in features
    assert "navigation.instant" not in features
    assert {
        "navigation.tabs",
        "navigation.tabs.sticky",
        "navigation.sections",
        "navigation.path",
        "navigation.tracking",
        "navigation.footer",
        "content.code.copy",
    } <= features
    assert {
        "admonition",
        "attr_list",
        "md_in_html",
        "pymdownx.details",
        "pymdownx.highlight",
        "pymdownx.superfences",
        "pymdownx.tabbed",
        "toc",
    } == extension_names

    assert config["theme"]["palette"] == {
        "scheme": "slate",
        "primary": "custom",
        "accent": "custom",
    }


def test_every_page_starts_with_a_visible_h1():
    for path in DOCS_DIR.rglob("*.md"):
        text = path.read_text()
        if text.startswith("---\n"):
            text = text.split("---\n", 2)[2]
        assert re.search(r"^# [^#]", text, re.MULTILINE), path.relative_to(DOCS_DIR)


def test_every_page_has_a_description_front_matter_field():
    for path in DOCS_DIR.rglob("*.md"):
        text = path.read_text()
        assert text.startswith("---\n"), path.relative_to(DOCS_DIR)
        front_matter = text.split("---\n", 2)[1]
        meta = yaml.safe_load(front_matter) or {}
        description = meta.get("description")
        assert isinstance(description, str) and description.strip(), path.relative_to(
            DOCS_DIR
        )


def test_homepage_uses_parsed_markdown_and_audience_actions():
    homepage = (DOCS_DIR / "index.md").read_text()

    assert "# OddsFox Graph" in homepage
    assert 'class="of-hero" markdown' in homepage
    assert "[Get started](getting-started/index.md)" in homepage
    assert homepage.count('class="of-task-card"') == 4
    assert "audiences/analysts.md" in homepage
    assert "audiences/operators.md" in homepage
    assert "audiences/contributors.md" in homepage
    assert "audiences/integrators.md" in homepage


def test_brand_assets_and_compact_styles_exist():
    assets = [
        "assets/images/oddsfox-white.png",
        "assets/images/oddsfox-favicon.png",
        "assets/fonts/inter-latin-variable.woff2",
        "assets/fonts/jetbrains-mono-latin-variable.woff2",
        "assets/stylesheets/extra.css",
        "assets/javascripts/mermaid.min.js",
        "assets/javascripts/MERMAID-LICENSE.txt",
        "assets/javascripts/mermaid.js",
    ]

    for target in assets:
        asset = DOCS_DIR / target
        assert asset.is_file(), target
        assert asset.stat().st_size > 0, target

    css = (DOCS_DIR / "assets/stylesheets/extra.css").read_text()
    assert css.count("@font-face") == 2
    assert ".of-hero" in css
    assert ".of-task-grid" in css
    assert ".of-persona" in css
    assert ".of-persona--analyst" in css


def test_readme_links_to_canonical_docs_and_local_preview():
    readme = (REPO_ROOT / "README.md").read_text()
    required = [
        "https://graph.oddsfox.io/",
        "uv sync --extra docs",
        "uv run mkdocs serve -a 127.0.0.1:8000",
        "(docs/getting-started/index.md)",
    ]

    for term in required:
        assert term in readme, term


def test_vercel_config_builds_mkdocs_site():
    import json

    config = json.loads((REPO_ROOT / "vercel.json").read_text())
    assert config["trailingSlash"] is True
    assert config["outputDirectory"] == "site"
    assert "mkdocs build --strict" in config["buildCommand"]
    assert config["redirects"] == []
