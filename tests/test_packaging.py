# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""What HACS and hassfest check, as tests that run offline: manifest, hacs.json, brand icon, translations, changelog."""
import json
import re
import struct
from pathlib import Path

from core import health

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "lutbridge"
MANIFEST = json.loads((PKG / "manifest.json").read_text())
STRINGS = json.loads((PKG / "strings.json").read_text())


def test_manifest_has_every_key_hacs_and_hassfest_require():
    for key in ("domain", "name", "codeowners", "config_flow", "dependencies", "documentation", "integration_type",
                "iot_class", "issue_tracker", "requirements", "version"):
        assert key in MANIFEST, key
    assert re.fullmatch(r"\d+\.\d+\.\d+", MANIFEST["version"])
    assert all(re.fullmatch(r"@[\w-]+", c) for c in MANIFEST["codeowners"]) and MANIFEST["codeowners"]
    assert MANIFEST["documentation"].startswith("https://") and MANIFEST["issue_tracker"].startswith("https://")


def test_manifest_keys_are_ordered_the_way_hassfest_wants():
    keys = list(MANIFEST)
    assert keys[:2] == ["domain", "name"] and keys[2:] == sorted(keys[2:])


def test_the_integration_folder_is_what_hacs_expects():
    assert MANIFEST["domain"] == PKG.name
    assert [p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir() and p.name != "__pycache__"] == ["lutbridge"]
    for f in ("__init__.py", "config_flow.py", "services.yaml", "strings.json", "translations/en.json", "manifest.json"):
        assert (PKG / f).is_file(), f


def test_hacs_json_uses_only_known_keys():
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert set(hacs) <= {"name", "homeassistant", "render_readme", "country", "zip_release", "filename", "hide_default_branch",
                         "persistent_directory", "content_in_root"}
    assert hacs["name"] and re.fullmatch(r"\d{4}\.\d+\.\d+", hacs["homeassistant"])


def test_brand_icons_are_square_pngs_of_the_right_size():
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        data = (PKG / "brand" / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (size, size), name


def test_every_health_problem_has_repairs_text():
    inputs = health.Inputs(bridge_known=False, z2m_online=False, board_online=False, heartbeat_ok=False, claim_age_s=999,
                           converter_current=False, changes_pending=1, on_fallback={"x": ["y"]}, firmware_supports_slots=False)
    keys = set(health.evaluate(inputs).problems)
    # heartbeat and the others are all reachable; make sure none is missing from the translations
    keys |= set(health.evaluate(health.Inputs(True, True, True, True, 999.0, True, 0)).problems)
    assert keys == set(STRINGS["issues"])
    for key, text in STRINGS["issues"].items():
        assert text["title"] and "{detail}" in text["description"], key


def test_every_service_is_described_in_the_translations():
    services = re.findall(r"^([a-z_]+):\s*$", (PKG / "services.yaml").read_text(), re.M)
    assert services and set(services) == set(STRINGS["services"])
    for name in services:
        assert STRINGS["services"][name]["name"] and STRINGS["services"][name]["description"]


def test_translation_files_match_and_source_runtime_keys_exist():
    assert json.loads((PKG / "translations" / "en.json").read_text()) == STRINGS
    runtime = (PKG / "runtime.py").read_text()
    assert "translation_key=key" in runtime  # issue keys come from health.evaluate, covered above


def test_the_license_is_apache_2_with_a_notice_and_every_source_file_says_so():
    license_text = (ROOT / "LICENSE").read_text()
    assert "Apache License" in license_text and "Version 2.0, January 2004" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text and len(license_text) == 11358  # the canonical text, unmodified
    notice = (ROOT / "NOTICE").read_text()
    assert "Copyright 2026 Dave Hammer" in notice and "trademarks of Lutron" in notice and "not affiliated" in notice
    missing = [str(p.relative_to(ROOT)) for pattern in ("custom_components/**/*.py", "tests/*.py", "custom_components/**/*.mjs")
               for p in ROOT.glob(pattern) if "SPDX-License-Identifier: Apache-2.0" not in p.read_text()]
    assert missing == []
    assert "Apache License, Version 2.0" in (ROOT / "README.md").read_text()


def test_the_changelog_covers_the_manifest_version():
    assert f"## {MANIFEST['version']}" in (ROOT / "CHANGELOG.md").read_text()


def test_workflows_exist_for_hassfest_hacs_and_tests():
    wf = ROOT / ".github" / "workflows"
    validate = (wf / "validate.yml").read_text()
    assert "home-assistant/actions/hassfest" in validate and "hacs/action" in validate and "category: integration" in validate
    assert "pytest" in (wf / "tests.yml").read_text()


def test_the_integration_points_at_the_bridge_project():
    from_const = (PKG / "const.py").read_text()
    assert "https://github.com/davemhammer/lutbridge-esp32" in from_const
    assert "lutbridge-esp32" in (ROOT / "README.md").read_text()
    assert "lutbridge-esp32" in STRINGS["config"]["step"]["user"]["description"]
    assert "lutbridge-esp32" in STRINGS["issues"]["board_not_found"]["description"]


def test_manifest_urls_match_this_repository():
    assert MANIFEST["documentation"] == "https://github.com/davemhammer/lutbridge-ha"
    assert MANIFEST["issue_tracker"] == "https://github.com/davemhammer/lutbridge-ha/issues"
    assert MANIFEST["codeowners"] == ["@davemhammer"]
