"""Build the Windows installer, SC application archive and matching source bundle."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

from fetch_inputs import ROOT, fetch_inputs


def run(command):
    print("RUN " + subprocess.list2cmdline([str(x) for x in command]), flush=True)
    subprocess.run([str(x) for x in command], cwd=ROOT, check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_tree(folder, output, prefix=""):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, prefix + path.relative_to(folder).as_posix())


def build_release(skip_freeze=False, development=False):
    if sys.version_info[:3] != (3, 14, 3):
        raise RuntimeError("Release runtime is pinned to CPython 3.14.3 x64.")
    cache = fetch_inputs()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    if status and not development:
        raise RuntimeError("Commit the release inputs first; use --development only for unpublished test builds.")
    version = json.loads((ROOT / "packaging/version.json").read_text())
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    # A failed rebuild must not leave a success manifest for mixed artifacts.
    (output / "SHA256SUMS.txt").unlink(missing_ok=True)
    frozen = ROOT / ".build/frozen/SuperSnipper"
    codec = ROOT / ".build/ffmpeg-build/installed/bin/ffmpeg.exe"
    if not codec.is_file():
        raise FileNotFoundError("Build the pinned FFmpeg first: python packaging/build_ffmpeg.py")
    if not skip_freeze:
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", ".build/frozen",
             "--workpath", ".build/pyinstaller", "packaging/SuperSnipper.spec"])
    payload = ROOT / ".build/payload/SuperSnipper"
    if payload.exists():
        if not payload.resolve().is_relative_to((ROOT / ".build/payload").resolve()):
            raise RuntimeError("Payload path escaped build folder")
        shutil.rmtree(payload)
    shutil.copytree(frozen, payload)
    (payload / "bin").mkdir()
    shutil.copy2(codec, payload / "bin/ffmpeg.exe")
    with zipfile.ZipFile(cache / "AutoHotkey_2.0.25.zip") as archive:
        (payload / "bin/AutoHotkey64.exe").write_bytes(archive.read("AutoHotkey64.exe"))
        ahk_licenses = payload / "licenses/AutoHotkey"
        ahk_licenses.mkdir(parents=True)
        (ahk_licenses / "license.txt").write_bytes(archive.read("license.txt"))
    for name in ("super_snipper_hotkey.ahk", "LICENSE", "README.md"):
        shutil.copy2(ROOT / name, payload / name)
    shutil.copy2(ROOT / "packaging/THIRD_PARTY_NOTICES.txt", payload / "THIRD_PARTY_NOTICES.txt")
    licenses = payload / "licenses"
    codec_info = subprocess.check_output([str(codec), "-version"], text=True,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
    (licenses / "FFmpeg-build.txt").write_text(codec_info, encoding="utf-8")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    shutil.copy2(ROOT / ".build/tools/w64devkit/COPYING.MinGW-w64-runtime.txt", licenses)
    # Collect complete wheel metadata, including nested third-party license files.
    for distribution in importlib.metadata.distributions():
        for relative in distribution.files or []:
            if ".dist-info/" in str(relative).replace("\\", "/"):
                target = licenses / "python-packages" / str(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(relative), target)
    # Tcl/Tk and PyInstaller license files are outside wheel metadata on Windows.
    for base in (Path(sys.base_prefix) / "tcl", Path(importlib.metadata.distribution("pyinstaller").locate_file("PyInstaller"))):
        for path in base.rglob("*"):
            if path.is_file() and ("license" in path.name.lower() or "copying" in path.name.lower()):
                target = licenses / "python-runtime" / base.name / path.relative_to(base)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    for name, group in (("ffmpeg-9.0.2.tar.xz", "FFmpeg"), ("x264-0480cb05.tar.gz", "x264"),
                        ("zlib-1.3.2.tar.gz", "zlib"), ("qtbase-everywhere-src-6.11.1.tar.xz", "Qt"),
                        ("qtsvg-everywhere-src-6.11.1.tar.xz", "QtSvg"),
                        ("qtimageformats-everywhere-src-6.11.1.tar.xz", "QtImageFormats"),
                        ("pyside-setup-everywhere-src-6.11.1.tar.xz", "PySide")):
        with tarfile.open(cache / name) as source:
            for member in source.getmembers():
                if not member.isfile():
                    continue
                relative = Path(*Path(member.name).parts[1:])
                if (relative.name.upper().startswith(("COPYING", "LICENSE")) or "LICENSES" in relative.parts
                        or (group == "zlib" and str(relative) == "zlib.h")):
                    target = licenses / group / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.extractfile(member).read())
    # The application's source is kept available beside its replaceable libraries.
    app_source = payload / "source"
    app_source.mkdir()
    for folder in ("supersnipper", "packaging"):
        shutil.copytree(ROOT / folder, app_source / folder, ignore=shutil.ignore_patterns("__pycache__", "_INDEX.md"))
    for name in ("super_snipper.py", "super_snipper_hotkey.ahk", "super_snipper_requirements.txt", "LICENSE"):
        shutil.copy2(ROOT / name, app_source / name)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    build_info = {**version, "source_commit": commit, "python": sys.version,
                  "development_build": development, "source_dirty": bool(status),
                  "payload_schema": 1, "gui": "SuperSnipper.exe", "cli": "SuperSnipperCLI.exe",
                  "signed": False, "ffmpeg_sha256": digest(payload / "bin/ffmpeg.exe")}
    source_hashes = {p.relative_to(app_source).as_posix(): digest(p) for p in sorted(app_source.rglob("*")) if p.is_file()}
    build_info["source_tree_sha256"] = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    (payload / "release-info.json").write_text(json.dumps(build_info, indent=2) + "\n", encoding="ascii")
    manifest = {p.relative_to(payload).as_posix(): digest(p) for p in sorted(payload.rglob("*")) if p.is_file()}
    (payload / "file-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="ascii")
    stem = "SuperSnipper-" + version["version"]
    archive_tree(payload, output / (stem + "-windows-x64.zip"), "SuperSnipper/")
    with zipfile.ZipFile(output / (stem + "-sources.zip"), "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(cache.iterdir()):
            if path.name.endswith((".tar.xz", ".tar.gz", "-source.zip")):
                archive.write(path, "upstream/" + path.name)
        for path in sorted(app_source.rglob("*")):
            if path.is_file():
                archive.write(path, "super-snipper/" + path.relative_to(app_source).as_posix())
        archive.write(payload / "THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.txt")
        archive.write(payload / "release-info.json", "release-info.json")
        archive.write(licenses / "FFmpeg-build.txt", "FFmpeg-build.txt")
        archive.write(ROOT / ".build/tools/w64devkit/Dockerfile", "toolchain/w64devkit-Dockerfile")
        archive.write(ROOT / ".build/tools/w64devkit/COPYING.MinGW-w64-runtime.txt", "toolchain/COPYING.MinGW-w64-runtime.txt")
    iscc = Path(os.environ.get("SUPER_SNIPPER_ISCC", Path(os.environ["LOCALAPPDATA"]) / "Programs/Inno Setup 6/ISCC.exe"))
    if not iscc.is_file():
        raise FileNotFoundError("Set SUPER_SNIPPER_ISCC to the Inno Setup 6 compiler.")
    run([iscc, "/DAppVersion=" + version["version"], "/DAppGuid=" + version["app_id"],
         "/DPayloadDir=" + str(payload), "/DOutputDir=" + str(output), ROOT / "packaging/SuperSnipper.iss"])
    artifacts = [output / (stem + suffix) for suffix in ("-windows-x64.zip", "-sources.zip", "-windows-x64-setup.exe")]
    (output / "SHA256SUMS.txt").write_text("".join(f"{digest(p)}  {p.name}\n" for p in artifacts), encoding="ascii")
    print("RELEASE_READY " + str(output), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-freeze", action="store_true", help="development only: reuse the existing frozen executable")
    parser.add_argument("--development", action="store_true", help="allow a dirty tree; artifact metadata marks it unpublishable")
    args = parser.parse_args()
    if args.skip_freeze and not args.development:
        parser.error("--skip-freeze requires --development")
    build_release(args.skip_freeze, args.development)
