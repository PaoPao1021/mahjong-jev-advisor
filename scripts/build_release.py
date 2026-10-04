"""Build, smoke-test, and archive a native portable distribution.

Run on each target OS using a clean Python 3.12 virtual environment.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = "MahjongJevAdvisor"


def build() -> None:
    if sys.platform not in ("win32", "darwin"):
        raise SystemExit("Portable release builds support Windows and macOS only")
    version = importlib.metadata.version("mahjong-jev-advisor")
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
    system = "windows" if sys.platform == "win32" else "macos"
    label = f"mahjong-jev-advisor-{version}-{system}-{arch}"
    stage = ROOT / "dist" / label
    if stage.exists():
        raise SystemExit(f"Output already exists; move or remove it before rebuilding: {stage}")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "--windowed", "--onedir", "--name", NAME,
               "--distpath", str(stage), "--workpath", str(ROOT / "build" / "freeze"),
               "--specpath", str(ROOT / "build"), "--paths", str(ROOT / "src"),
               "--add-data", f"{ROOT / 'src/mahjong_jev_advisor/liqi.json'}:mahjong_jev_advisor",
               "--collect-all", "rapidocr_onnxruntime", "--collect-all", "onnxruntime",
               "--collect-submodules", "mahjong", "--osx-bundle-identifier", "io.github.paopao1021.mahjong-jev-advisor"]
    for package in ("PySide6", "opencv-python-headless", "numpy", "mss", "rapidocr-onnxruntime", "mahjong", "mahjong-jev-advisor"):
        command += ["--copy-metadata", package]
    command.append(str(ROOT / "packaging" / "launcher.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    if sys.platform == "darwin":
        # PyInstaller emits both COLLECT and BUNDLE outputs; distribute only .app.
        shutil.rmtree(stage / NAME)
        executable = stage / f"{NAME}.app" / "Contents" / "MacOS" / NAME
        content = stage
    else:
        executable = stage / NAME / f"{NAME}.exe"
        content = stage / NAME
    shutil.copytree(ROOT / "browser-extension", content / "browser-extension",
                    ignore=shutil.ignore_patterns(".DS_Store", "*.pem", "*.crx"))
    shutil.copy2(ROOT / "packaging" / "QUICKSTART.txt", content)
    shutil.copy2(ROOT / "LICENSE", content)
    licenses = content / "third-party-licenses"
    licenses.mkdir()
    inventory = []
    for dist in importlib.metadata.distributions():
        package = dist.metadata["Name"]
        inventory.append({"name": package, "version": dist.version})
        for file in dist.files or []:
            if not (".dist-info/" in str(file) or ".egg-info/" in str(file)):
                continue
            if not any(x in str(file).lower() for x in ("license", "copying", "notice")):
                continue
            source = Path(dist.locate_file(file))
            if source.is_file():
                target = licenses / package / Path(*file.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    (licenses / "DEPENDENCIES.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    report = ROOT / "build" / f"{label}-smoke.json"
    subprocess.run([str(executable), "--smoke-test", str(report)], cwd=ROOT / "build", check=True, timeout=180)
    result = json.loads(report.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise SystemExit(f"Frozen smoke test failed: {result}")
    shutil.copy2(report, content / "build-verification.json")
    archive = ROOT / "dist" / f"{label}.zip"
    if sys.platform == "darwin":
        # ditto preserves .app symlinks and executable modes.
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(stage), str(archive)], check=True)
    else:
        shutil.make_archive(str(archive.with_suffix("")), "zip", root_dir=stage)
    digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Release candidate: {archive}\nSHA256: {digest}")


if __name__ == "__main__":
    build()
