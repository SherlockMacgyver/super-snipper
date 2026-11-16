# Super Snipper project history

<a id="history/packaging-1-0-0"></a>
## Packaging before release 1.0.1

Frozen packaging entry from commit 54a15e13f1059c49bceb9b5846c246ed5e359e07.
Current state remains in PROJECT_DOC.md.

```markdown
<a id="project/packaging"></a>
### Windows installer and application archive

**Root summary:** Installer and archive bundle dependencies. Host installation, recording, export, repair and removal passed; clean-OS, tray/hotkeys and sign-in remain unverified. Windows may warn or block unsigned builds.

`packaging/version.json` owns the release version. `inputs.json` pins downloaded
source/tool inputs; `requirements-build.txt` pins Python packages. PyInstaller
creates GUI/console executables with replaceable Qt DLLs. `config.launch_command`
owns child relaunch; frozen FFmpeg resolves only `bin/ffmpeg.exe`. Source behavior
and command-line interfaces remain supported.

`build_ffmpeg.py` compiles FFmpeg with x264 and zlib from published source, using
the pinned portable compiler and NASM. `build_release.py` assembles one payload,
source/notices bundle, file hashes, application ZIP and Inno Setup package.
Production builds require a clean tree; development builds identify their state.
The public installer uses per-user versioned folders, optional sign-in/desktop
shortcuts and refuses modification while its application is running. Uninstall
keeps captures and application settings/logs.

SC's current source-archive pin is untouched. The binary archive contract is in
README, for the separate SC integration follow-up. Source: installer claim
e36698eeac42420aa2f4de9fad353d59. Release verification is recorded in that checklist;
source tests and developer-host isolation are not clean-OS acceptance.

Actual host setup, screenshot, H.264/AAC recording, video storyboard, running-app
refusal, repair and uninstall passed with restricted PATH. A synthetic older
version verified directory transition, shortcut target and removal of both
versions while retaining captures/settings. The packaged series window launched
its selector child and recovered from cancellation; successful interactive
selection was not witnessed. Desktop interaction stopped at the owner's request.

```
