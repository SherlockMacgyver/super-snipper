"""Source compatibility and optional real packaged executable checks.

Set SUPER_SNIPPER_PACKAGE to a built payload folder for executable tests.
Run packaging/verify_release.py for actual setup/upgrade/uninstall acceptance.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_source_relaunch_contract_unchanged():
    from supersnipper import config, hud_widget
    args = ["--snip", "--snip-output", "path with spaces.txt"]
    assert not config.FROZEN
    assert hud_widget.hud_launch_command(args) == [sys.executable, str(config.ENTRY_SCRIPT), *args]


def test_packaged_command_uses_gui_binary_for_all_children(monkeypatch, tmp_path):
    from supersnipper import config, hud_widget, recording
    monkeypatch.setattr(config, "FROZEN", True)
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    for args in (["--panel-service"], ["--record-box", "0,0,640,360"], ["--storyboard"],
                 ["--series"], ["--snip", "--snip-output", "out.txt"]):
        assert hud_widget.hud_launch_command(args) == [str(tmp_path / "SuperSnipper.exe"), *args]
    monkeypatch.setenv("SUPER_SNIPPER_FFMPEG", str(tmp_path / "unrelated.exe"))
    with pytest.raises(FileNotFoundError, match="Bundled FFmpeg"):
        recording.resolve_ffmpeg()
    (tmp_path / "bin").mkdir()
    codec = tmp_path / "bin/ffmpeg.exe"
    codec.write_bytes(b"fixture")
    assert recording.resolve_ffmpeg() == codec


@pytest.fixture
def packaged():
    value = os.environ.get("SUPER_SNIPPER_PACKAGE")
    if not value:
        pytest.skip("Set SUPER_SNIPPER_PACKAGE to exercise built binaries")
    return Path(value).resolve()


def test_real_binary_uses_bundled_language_and_gui_runtime(packaged, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "QT_", "SUPER_SNIPPER"))}
    env.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
               SUPER_SNIPPER_HOME=str(tmp_path / "state"),
               SUPER_SNIPPER_FFMPEG=str(tmp_path / "does-not-exist.exe"))
    report = tmp_path / "report.json"
    result = subprocess.run([str(packaged / "SuperSnipperCLI.exe"), "--diagnostics", str(report)],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text())
    assert data["frozen"]
    assert Path(data["ffmpeg"]) == packaged / "bin/ffmpeg.exe"
    assert data["child_command"] == [str(packaged / "SuperSnipper.exe"), "--self-test"]
    external = []
    for path in data["loaded_files"]:
        lower = path.lower()
        if lower.endswith((".dll", ".pyd")) and not lower.startswith(str(packaged).lower()):
            if not lower.startswith(os.environ["SystemRoot"].lower()):
                external.append(path)
                # Windows may inject endpoint-protection providers (AMSI).
                # They are not language/GUI dependencies shipped by this app.
                assert not lower.startswith(str(Path(sys.base_prefix)).lower()), path
                assert not lower.endswith(".pyd"), path
                assert not Path(path).name.lower().startswith(("python", "qt6", "pyside", "shiboken")), path
    print("External host modules (not clean-OS evidence):", external)


def test_payload_manifest_and_licenses(packaged):
    import hashlib
    manifest = json.loads((packaged / "file-manifest.json").read_text())
    for name, expected in manifest.items():
        assert hashlib.sha256((packaged / name).read_bytes()).hexdigest() == expected, name
    assert (packaged / "licenses/AutoHotkey/license.txt").is_file()
    assert (packaged / "licenses/FFmpeg/COPYING.GPLv2").is_file()
    assert (packaged / "licenses/Qt/LICENSES/LGPL-3.0-only.txt").is_file()
