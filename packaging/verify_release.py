"""Exercise actual install, repair/upgrade and uninstall in a disposable directory.

Refuses to overwrite an existing packaged install, registry key or shortcut.
Uses this Windows account's temporary app registration, guarded by absence checks.
Does not start the tray or change startup preferences. Preserves captured files.
This is installed-app evidence on the host, not a clean Windows VM witness.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import winreg

ROOT = Path(__file__).resolve().parents[1]


def verify_release(previous_setup=None, previous_version=None, no_desktop=False):
    version = json.loads((ROOT / "packaging/version.json").read_text())
    setup = ROOT / "dist" / f"SuperSnipper-{version['version']}-windows-x64-setup.exe"
    key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{version['app_id']}_is1"
    for candidate in (key, r"Software\SuperSnipper"):
        try:
            handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, candidate)
        except FileNotFoundError:
            pass
        else:
            handle.Close()
            raise RuntimeError(f"Existing installation registration: {candidate}. Use a disposable Windows user.")
    shortcuts = [Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Super Snipper.lnk",
                 Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/Super Snipper.lnk"]
    if any(path.exists() for path in shortcuts):
        raise RuntimeError("Existing shortcut; refusing to overwrite it in an installation test")
    scratch = Path(tempfile.mkdtemp(prefix="super-snipper-installed-check-"))
    install = scratch / "Installed App"
    profile = scratch / "profile"
    profile.mkdir()
    # Inno expands user shell folders against USERPROFILE in this isolated run.
    shortcuts = [profile / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Super Snipper.lnk",
                 profile / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/Super Snipper.lnk"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "QT_", "SUPER_SNIPPER"))}
    env.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
               SUPER_SNIPPER_HOME=str(scratch / "state"), USERPROFILE=str(profile),
               SUPER_SNIPPER_INSTANCE="verify" + str(os.getpid()))
    payload = install / "versions" / version["version"]
    flags = subprocess.CREATE_NO_WINDOW

    def run(args, expected=0, timeout=120):
        result = subprocess.run([str(x) for x in args], env=env, cwd=scratch, capture_output=True,
                                text=True, timeout=timeout, creationflags=flags)
        if result.returncode != expected:
            raise RuntimeError(f"Unexpected exit {result.returncode}: {args}\n{result.stdout}\n{result.stderr}")
        return result

    args = [setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
            "/DIR=" + str(install), "/TASKS=", "/LOG=" + str(scratch / "install.log")]
    print("VERIFY_ROOT=" + str(scratch), flush=True)
    if previous_setup:
        run([previous_setup, *args[1:]])
        assert (install / "versions" / previous_version / "SuperSnipper.exe").is_file()
        (scratch / "state").mkdir(exist_ok=True)
        (scratch / "state/upgrade-setting.txt").write_text("preserve across versions")
        (profile / "Pictures/Screenshots").mkdir(parents=True, exist_ok=True)
        (profile / "Pictures/Screenshots/upgrade-capture.txt").write_text("preserve capture")
        print("EARLIER_VERSION_INSTALLED", flush=True)
    run(args)
    print("FRESH_INSTALL_OK", flush=True)
    assert (payload / "bin/ffmpeg.exe").is_file()
    assert shortcuts[0].is_file() and not shortcuts[1].exists()
    link = shortcuts[0].read_bytes()
    target = str(payload / "SuperSnipper.exe")
    assert target.encode("utf-16le") in link or target.encode("ascii") in link
    if previous_setup:
        assert (scratch / "state/upgrade-setting.txt").read_text() == "preserve across versions"
        assert (profile / "Pictures/Screenshots/upgrade-capture.txt").read_text() == "preserve capture"
        print("VERSION_TRANSITION_SHORTCUT_AND_DATA_OK", flush=True)
    cli = payload / "SuperSnipperCLI.exe"
    report = scratch / "diagnostics.json"
    run([cli, "--diagnostics", report])
    data = json.loads(report.read_text())
    assert data["frozen"] and Path(data["ffmpeg"]) == payload / "bin/ffmpeg.exe"
    assert all(not p.lower().startswith(str(ROOT).lower()) for p in data["loaded_files"])
    print("INSTALLED_RUNTIME_DIAGNOSTICS_OK", flush=True)
    if no_desktop:
        run([install / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        assert not (payload / "SuperSnipper.exe").exists()
        if previous_setup:
            assert not (install / "versions" / previous_version / "SuperSnipper.exe").exists()
            assert (scratch / "state/upgrade-setting.txt").read_text() == "preserve across versions"
            assert (profile / "Pictures/Screenshots/upgrade-capture.txt").read_text() == "preserve capture"
        assert all(not path.exists() for path in shortcuts)
        print("RESULT background install/version-transition/uninstall passed; desktop capture deliberately skipped", flush=True)
        return
    run([cli, "--capture-box", "0,0,320,180", "--no-clipboard"])
    snips = list((profile / "Pictures/Screenshots").glob("*.jpg"))
    assert len(snips) == 1
    original = hashlib.sha256(snips[0].read_bytes()).hexdigest()
    print("INSTALLED_SNIP_OK", flush=True)
    run([cli, "--record-box", "0,0,640,360", "--duration", "2", "--countdown", "0", "--fps", "10"])
    videos = list((profile / "Videos/Captures").glob("*.mp4"))
    assert len(videos) == 1
    run([cli, "--storyboard", videos[0], "--sample-rate", "1", "--sample-unit", "second", "--sheet-edge", "512"])
    assert list(videos[0].parent.glob("*storyboard*.png"))
    print("INSTALLED_AUDIO_RECORDING_AND_STORYBOARD_OK", flush=True)
    # A running panel must prevent installer replacement. Exit via its own IPC door.
    run([cli, "--panel-command", "show"])
    try:
        result = subprocess.run([str(x) for x in args], env=env, cwd=scratch, capture_output=True,
                                timeout=60, creationflags=flags)
        assert result.returncode != 0, "Installer overwrote a running app"
        print("RUNNING_APP_UPGRADE_REFUSED", flush=True)
    finally:
        run([cli, "--panel-command", "exit"])
    time.sleep(0.5)
    # Reinstall/upgrade shares the same AppId and directory, preserving captures/settings.
    sentinel = scratch / "state/user-setting.txt"
    sentinel.write_text("keep this")
    run(args)
    assert sentinel.read_text() == "keep this"
    assert hashlib.sha256(snips[0].read_bytes()).hexdigest() == original
    print("SAME_VERSION_REPAIR_PRESERVES_DATA_OK", flush=True)
    uninstall = install / "unins000.exe"
    run([uninstall, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=" + str(scratch / "uninstall.log")])
    assert not (payload / "SuperSnipper.exe").exists()
    if previous_setup:
        assert not (install / "versions" / previous_version / "SuperSnipper.exe").exists()
        print("TWO_VERSION_UNINSTALL_OK", flush=True)
    assert all(not path.exists() for path in shortcuts)
    assert sentinel.read_text() == "keep this" and snips[0].is_file() and videos[0].is_file()
    print("UNINSTALL_PRESERVES_CAPTURES_AND_SETTINGS_OK", flush=True)
    print("RESULT installed acceptance passed; clean-OS, sign-in startup and native input remain separate witnesses", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-setup", type=Path)
    parser.add_argument("--previous-version")
    parser.add_argument("--no-desktop", action="store_true", help="installation/diagnostics only; no capture or visible app windows")
    args = parser.parse_args()
    if bool(args.previous_setup) != bool(args.previous_version):
        parser.error("Pass both --previous-setup and --previous-version")
    verify_release(args.previous_setup, args.previous_version, args.no_desktop)
