"""Publication invariants are executable, not only documentation claims."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_strings_and_english_translation_stay_identical():
    component = ROOT / "custom_components" / "gwm_thailand_connect"
    assert json.loads((component / "strings.json").read_text()) == json.loads(
        (component / "translations" / "en.json").read_text(),
    )


def test_version_and_dashboard_resource_are_consistent():
    component = ROOT / "custom_components" / "gwm_thailand_connect"
    version = json.loads((component / "manifest.json").read_text())["version"]
    assert f'VERSION = "{version}"' in (component / "const.py").read_text()
    assert f"gwm-thailand-card.js?v={version}" in (ROOT / "docs" / "installation.md").read_text()
    assert f"gwm-journey-card.js?v={version}" in (
        component / "frontend" / "gwm-thailand-card.js"
    ).read_text()


def test_all_local_markdown_links_and_screens_exist():
    missing = []
    for path in [*ROOT.glob("*.md"), *ROOT.joinpath("docs").glob("*.md")]:
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            target = target.partition("#")[0]
            if target and not (path.parent / target).is_file():
                missing.append(f"{path.name}: {target}")
    assert not missing, missing


@pytest.mark.parametrize("name", ["material", "method", "code", "password", "consent", "verification", "options", "dashboard", "confirmation"])
def test_manual_screens_are_explicitly_generated_synthetic_fixtures(name):
    assert (ROOT / "docs" / "images" / f"{name}.png").is_file()
    assert name in (ROOT / "scripts" / "screenshots.mjs").read_text()
    demo = (ROOT / "docs" / "demo" / "index.html").read_text(encoding="utf-8")
    assert "SYNTHETIC DATA" in demo and "demo@example.invalid" in demo
