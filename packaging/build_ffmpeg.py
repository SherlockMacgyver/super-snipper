"""Build the distributed FFmpeg from the exact source inputs in inputs.json.

Only external codec library: x264. PNG compression: zlib. The full source
archives and this recipe accompany the release, including toolchain notices.
"""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

from fetch_inputs import fetch_inputs, ROOT


def short_path(path):
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path.resolve()), buffer, len(buffer)):
        raise ctypes.WinError()
    value = buffer.value.replace("\\", "/")
    if " " in value:
        raise RuntimeError("FFmpeg build needs a path without spaces; enable short paths or use a space-free checkout.")
    return value


def build_ffmpeg():
    cache = fetch_inputs()
    tools = ROOT / ".build" / "tools"
    source = ROOT / ".build" / "ffmpeg-build"
    prefix = source / "installed"
    for folder in (tools, source, prefix / "include", prefix / "lib"):
        folder.mkdir(parents=True, exist_ok=True)
    if not (tools / "w64devkit/bin/gcc.exe").is_file():
        subprocess.run([str(cache / "w64devkit.7z.exe"), "-y", "-o" + str(tools)], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if not (tools / "nasm-3.01/nasm.exe").is_file():
        with zipfile.ZipFile(cache / "nasm-3.01-win64.zip") as archive:
            archive.extractall(tools)
    sources = {}
    for name in ("x264-0480cb05.tar.gz", "ffmpeg-9.0.2.tar.xz", "zlib-1.3.2.tar.gz"):
        with tarfile.open(cache / name) as archive:
            top = archive.getnames()[0].split("/")[0]
            if not (source / top).is_dir():
                archive.extractall(source, filter="data")
            sources[name.split("-")[0]] = source / top
    bindir = tools / "w64devkit/bin"
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(bindir), str(tools / "nasm-3.01"), os.environ["SystemRoot"] + "\\System32"])
    env["PKG_CONFIG_PATH"] = short_path(prefix) + "/lib/pkgconfig"
    env["CC"] = "gcc"
    temp = source / "tmp"
    temp.mkdir(exist_ok=True)
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = short_path(temp)
    dest = short_path(prefix)

    def run(folder, command):
        print(command, flush=True)
        subprocess.run([str(bindir / "sh.exe"), "-c", command], cwd=short_path(folder),
                       env=env, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

    jobs = min(os.cpu_count() or 2, 8)
    run(sources["zlib"], f"make -f win32/Makefile.gcc -j{jobs} libz.a")
    for name in ("zlib.h", "zconf.h"):
        shutil.copy2(sources["zlib"] / name, prefix / "include" / name)
    shutil.copy2(sources["zlib"] / "libz.a", prefix / "lib" / "libz.a")
    run(sources["x264"], f"sh configure --host=x86_64-w64-mingw32 --enable-static --disable-cli --disable-opencl --prefix={dest}")
    run(sources["x264"], f"make -j{jobs} && make install")
    run(sources["ffmpeg"], f"sh configure --target-os=mingw32 --arch=x86_64 --enable-gpl --enable-libx264 "
        f"--enable-zlib --disable-autodetect --enable-static --disable-shared --disable-debug --disable-doc "
        f"--disable-ffplay --disable-network --extra-cflags=-I{dest}/include --extra-ldflags=-L{dest}/lib --prefix={dest}")
    run(sources["ffmpeg"], f"make -j{jobs} && make install")
    binary = prefix / "bin/ffmpeg.exe"
    result = subprocess.run([str(binary), "-hide_banner", "-encoders"], check=True, capture_output=True, text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if "libx264" not in result.stdout or " png " not in result.stdout:
        raise RuntimeError("Built FFmpeg lacks required x264 or PNG encoder")
    print("BUILT_FFMPEG=" + str(binary), flush=True)
    return binary


if __name__ == "__main__":
    build_ffmpeg()
