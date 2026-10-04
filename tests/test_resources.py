from pathlib import Path

from mahjong_jev_advisor import resources


def test_source_extension_path():
    assert (resources.extension_dir() / 'manifest.json').is_file()


def test_windows_portable_extension_path(monkeypatch, tmp_path):
    executable = tmp_path / 'MahjongJevAdvisor.exe'
    monkeypatch.setattr(resources.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(resources.sys, 'executable', str(executable))
    assert resources.extension_dir() == tmp_path / 'browser-extension'


def test_macos_portable_extension_path(monkeypatch, tmp_path):
    executable = tmp_path / 'MahjongJevAdvisor.app' / 'Contents' / 'MacOS' / 'MahjongJevAdvisor'
    monkeypatch.setattr(resources.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(resources.sys, 'platform', 'darwin')
    monkeypatch.setattr(resources.sys, 'executable', str(executable))
    assert resources.extension_dir() == tmp_path / 'browser-extension'
