import json

import pytest

from mahjong_jev_advisor import edition
from mahjong_jev_advisor.settings import Settings, config_dir


@pytest.mark.parametrize("selected", ["browser", "steam"])
def test_source_edition_defaults_and_persistence(monkeypatch, selected):
    monkeypatch.setenv("MAHJONG_ADVISOR_EDITION", selected)
    assert Settings.load().input_source == selected
    settings = Settings(api_key="edition-only")
    settings.save()
    assert Settings.load().input_source == selected
    assert Settings.load().api_key == "edition-only"


def test_steam_configuration_is_separate_and_browser_keeps_legacy_path(monkeypatch):
    monkeypatch.delenv("MAHJONG_ADVISOR_CONFIG_DIR")
    legacy = config_dir()
    monkeypatch.setenv("MAHJONG_ADVISOR_EDITION", "browser")
    assert config_dir() == legacy
    monkeypatch.setenv("MAHJONG_ADVISOR_EDITION", "steam")
    assert config_dir() == legacy.with_name("mahjong-jev-advisor-steam")
    monkeypatch.setenv("MAHJONG_ADVISOR_CONFIG_DIR", "/tmp/explicit-configuration")
    assert config_dir().name == "explicit-configuration"


def test_bundle_identity_wins_over_source_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(edition, "__file__", str(tmp_path / "edition.py"))
    (tmp_path / "_edition.json").write_text(json.dumps({"edition": "steam"}))
    monkeypatch.setenv("MAHJONG_ADVISOR_EDITION", "browser")
    assert edition.launch_edition() == "steam"
    assert Settings().input_source == "steam"


def test_invalid_source_edition_rejected(monkeypatch):
    monkeypatch.setenv("MAHJONG_ADVISOR_EDITION", "typo")
    with pytest.raises(ValueError):
        Settings.load()
