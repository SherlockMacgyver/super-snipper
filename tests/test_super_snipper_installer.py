"""Installer preflight checks and contained failure tests.

All installer invocations use _run_preflight, with -PreflightOnly and a private
SUPER_SNIPPER_HOME. Failure/mutation tests use a temporary source copy whose
entire installation section is replaced by a throwing sentinel. Thus a broken
preflight exit cannot modify the user's settings or tray. AST checks also keep
mutating operations in the real source below that exit.
"""

from __future__ import annotations

from contextlib import contextmanager

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_super_snipper.ps1"
LAUNCHER = ROOT / "super_snipper.py"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer")

PARSER_HARNESS = r"""
param([string]$Installer, [string]$CasesJson)
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$addType = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
    $n.GetCommandName() -eq 'Add-Type' }, $true) | Select-Object -First 1
$parser = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $n.Name -eq 'Get-ExecutedPythonScript' }, $true) | Select-Object -First 1
Invoke-Expression $addType.Extent.Text
Invoke-Expression $parser.Extent.Text
$results = foreach ($case in (Get-Content -Raw $CasesJson | ConvertFrom-Json)) {
    $executed = Get-ExecutedPythonScript $case.command $case.image
    if ($null -eq $executed) { '' } else { $executed }
}
@($results) | ConvertTo-Json -Compress
"""

PARSER_CASES = [
    ("pythonw.exe", r'"C:\Python314\pythonw.exe" "C:\Program Files\Snip\super_snipper.py" --panel-service',
     r"C:\Program Files\Snip\super_snipper.py"),
    ("python.exe", r"python -X utf8 C:\a\super_snipper.py --snip", r"C:\a\super_snipper.py"),
    ("python.exe", r"python -Xutf8 -u C:\a\super_snipper.py", r"C:\a\super_snipper.py"),
    ("python.exe", r"python -W ignore C:\a\inspect.py C:\a\super_snipper.py", r"C:\a\inspect.py"),
    ("python.exe", r'python -c "import time; time.sleep(60)" C:\a\super_snipper.py', None),
    ("python.exe", r"python -m pytest tests\test_super_snipper.py", None),
    ("python.exe", r'python -Bc "x" super_snipper.py', None),
    ("python.exe", r"python -- super_snipper.py", "super_snipper.py"),
    ("python.exe", r"python - super_snipper.py", None),
    ("py.exe", r"py -3 C:\a\super_snipper.py", r"C:\a\super_snipper.py"),
    ("py.exe", r"py -3.14-64 -E super_snipper.py", "super_snipper.py"),
    ("py.exe", r"py -V:3.14 super_snipper.py", "super_snipper.py"),
    ("python.exe", r"python --check-hash-based-pycs always super_snipper.py", "super_snipper.py"),
    ("python.exe", r"python", None),
]


def test_parser_finds_only_the_executed_script(tmp_path):
    harness = tmp_path / "harness.ps1"
    harness.write_text(PARSER_HARNESS, encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"image": i, "command": c} for i, c, _ in PARSER_CASES]), encoding="utf-8")
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
                             "-Installer", str(INSTALLER), "-CasesJson", str(cases)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    got = [value or None for value in json.loads(result.stdout)]
    assert got == [expected for _, _, expected in PARSER_CASES]


BOUNDARY_HARNESS = r"""
param([string]$Installer)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$preflight = @($ast.EndBlock.Statements | Where-Object {
    $_ -is [System.Management.Automation.Language.IfStatementAst] -and
    $_.Clauses[0].Item1.Extent.Text -eq '$PreflightOnly'
})
if ($preflight.Count -ne 1) { throw 'Expected exactly one top-level preflight branch' }
$exits = @($preflight[0].Clauses[0].Item2.Statements | Where-Object {
    $_ -is [System.Management.Automation.Language.ExitStatementAst] -and $_.Pipeline.Extent.Text -eq '0'
})
if ($exits.Count -ne 1) { throw 'Preflight must exit successfully' }
$parameter = $ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'PreflightOnly' }
if ($parameter.StaticType -ne [System.Management.Automation.SwitchParameter] -or $parameter.DefaultValue) {
    throw 'Preflight must be an opt-in switch'
}
$boundary = $preflight[0].Extent.EndOffset
# Fail closed on new preflight operations, including indirect executable calls
# and .NET writes that a mutation-name denylist alone would miss.
$allowedCommands = @('Add-Type', 'Get-Command', 'Join-Path', 'Split-Path', 'Test-Path',
    'Get-CimInstance', 'Where-Object', 'ForEach-Object', 'Select-Object',
    'Write-Host', 'Write-Warning', 'Assert-NoRunningSnipper', 'Get-ExecutedPythonScript')
$before = $ast.FindAll({ param($n) $n.Extent.StartOffset -lt $boundary }, $true)
foreach ($node in $before) {
    if ($node -is [System.Management.Automation.Language.CommandAst]) {
        $name = $node.GetCommandName()
        $safePython = $node.Extent.Text -in @('& $Python -c "import PySide6, PIL, numpy, psutil, soundcard"',
                                             '& $Python $Snipper --self-test')
        if ($name -notin $allowedCommands -and -not $safePython) {
            throw ('Unexpected preflight command: ' + $node.Extent.Text)
        }
    }
    if ($node -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
        $node.Member.Extent.Text -notin @('Split', 'StartsWith', 'GetFileName', 'GetEnvironmentVariable')) {
        throw ('Unexpected preflight method: ' + $node.Extent.Text)
    }
    if ($node -is [System.Management.Automation.Language.RedirectionAst]) {
        throw 'Preflight must not redirect to persistent files'
    }
}
$mutations = @($ast.FindAll({ param($n)
    ($n -is [System.Management.Automation.Language.CommandAst] -and
        $n.GetCommandName() -match '^(Register-ScheduledTask|Unregister-ScheduledTask|Stop-ScheduledTask|Start-ScheduledTask|Stop-Process|Start-Process|Install-StartupShortcut|Start-HotkeyProcess|Stop-ExistingHotkeyProcess)$') -or
    ($n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
        $n.Member.Extent.Text -in @('SetEnvironmentVariable', 'Save'))
}, $true))
if ($mutations.Count -lt 8) { throw 'Installation mutation inventory unexpectedly empty' }
foreach ($mutation in $mutations) {
    if ($mutation.Extent.StartOffset -le $boundary) { throw ('Mutation above preflight exit: ' + $mutation.Extent.Text) }
}
$guard = @($ast.EndBlock.Statements | Where-Object { $_.Extent.Text -eq 'Assert-NoRunningSnipper' })
if ($guard.Count -ne 1 -or $guard[0].Extent.EndOffset -ge $preflight[0].Extent.StartOffset) {
    throw 'Guard must run before preflight exit'
}
$selfTest = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
    $n.Extent.Text -eq '& $Python $Snipper --self-test' }, $true))
if ($selfTest.Count -ne 1 -or $selfTest[0].Extent.EndOffset -ge $guard[0].Extent.StartOffset) {
    throw 'Self-test must run before guard'
}
'BOUNDARY_OK'
"""


def _powershell(*arguments, env=None):
    return subprocess.run(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', *map(str, arguments)],
        capture_output=True, text=True, timeout=90, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _check_boundary(tmp_path, installer=INSTALLER):
    harness = tmp_path / 'boundary.ps1'
    harness.write_text(BOUNDARY_HARNESS, encoding='utf-8')
    return _powershell('-File', harness, '-Installer', installer)


def test_real_source_keeps_mutations_below_opt_in_preflight_exit(tmp_path):
    result = _check_boundary(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_boundary_check_rejects_write_above_exit_without_executing_it(tmp_path):
    source = INSTALLER.read_text(encoding='utf-8')
    source = source.replace('Assert-NoRunningSnipper\n\n',
                            '[Environment]::SetEnvironmentVariable("FORBIDDEN", "1", "User")\n'
                            'Assert-NoRunningSnipper\n\n')
    broken = tmp_path / 'early-write.ps1'
    broken.write_text(source, encoding='utf-8')
    result = _check_boundary(tmp_path, broken)
    assert result.returncode != 0
    assert 'Unexpected preflight method' in result.stderr


def _contained_copy(tmp_path, *, defeat_guard=False, remove_exit=False):
    """Keep real startup/checks; remove ALL installation code before mutations."""
    boundary = _check_boundary(tmp_path)
    assert boundary.returncode == 0, boundary.stdout + boundary.stderr
    target = tmp_path / 'scripts'
    target.mkdir()
    shutil.copytree(ROOT / 'supersnipper', target / 'supersnipper',
                    ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('super_snipper.py', 'super_snipper_hotkey.ahk', 'super_snipper_requirements.txt'):
        shutil.copy2(ROOT / name, target / name)
    source = INSTALLER.read_text(encoding='utf-8')
    marker = '# Installation changes begin here.'
    assert source.count(marker) == 1, 'Refuse to construct an uncontained installer'
    source = source[:source.index(marker)] + "throw 'INSTALLATION_BOUNDARY_REACHED'\n"
    # A second, independent check ensures even a moved write cannot reach this copy.
    assert 'SetEnvironmentVariable' not in source and 'Stop-Process' not in source
    assert 'Start-Process' not in source and '.Save()' not in source
    assert 'Register-ScheduledTask' not in source and 'Unregister-ScheduledTask' not in source
    if defeat_guard:
        assert source.count('Assert-NoRunningSnipper\n\n') == 1
        source = source.replace('Assert-NoRunningSnipper\n\n', '# Test-only guard bypass\n\n')
    if remove_exit:
        assert source.count('    exit 0\n') == 1
        source = source.replace('    exit 0\n', '')
    installer = target / 'install_super_snipper.ps1'
    installer.write_text(source, encoding='utf-8')
    return installer


def _run_preflight(installer, tmp_path):
    """The sole installer invocation door in tests: never install, never use live logs."""
    env = dict(os.environ, SUPER_SNIPPER_HOME=str(tmp_path / 'home'),
               SUPER_SNIPPER_FFMPEG=str(tmp_path / 'missing-ffmpeg.exe'))
    return _powershell('-File', installer, '-Python', sys.executable, '-PreflightOnly', env=env)


def _install_state():
    result = _powershell('-Command', r'''
$task = Get-ScheduledTask -TaskName SuperSnipperHotkey -ErrorAction SilentlyContinue
$shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Super Snipper Hotkey.lnk'
@{
    pythonw = [Environment]::GetEnvironmentVariable('SUPER_SNIPPER_PYTHONW', 'User')
    ffmpeg = [Environment]::GetEnvironmentVariable('SUPER_SNIPPER_FFMPEG', 'User')
    task = if ($task) { Export-ScheduledTask -TaskName SuperSnipperHotkey } else { 'absent' }
    shortcut = if (Test-Path -LiteralPath $shortcut) { (Get-FileHash -LiteralPath $shortcut).Hash } else { 'absent' }
    tray = @((Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'AutoHotkey*' -and
        $_.CommandLine -like '*super_snipper_hotkey.ahk*' } | Sort-Object ProcessId).ProcessId)
} | ConvertTo-Json -Compress
''')
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('remove_exit', [False, True])
def test_guard_defeated_copy_cannot_change_installation(tmp_path, remove_exit):
    installer = _contained_copy(tmp_path, defeat_guard=True, remove_exit=remove_exit)
    before = _install_state()
    result = _run_preflight(installer, tmp_path)
    assert _install_state() == before
    output = result.stdout + result.stderr
    assert 'VIDEO RECORDING IS UNAVAILABLE' in output, output
    if remove_exit:
        assert result.returncode != 0 and 'INSTALLATION_BOUNDARY_REACHED' in output
    else:
        assert result.returncode == 0, output
        assert 'Preflight passed. No installation changes were made.' in output


@contextmanager
def _owned_processes():
    """Track before later launches can fail; reap only these roots and descendants."""
    roots = []

    def start(*args, **kwargs):
        process = psutil.Popen(*args, creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)
        roots.append(process)
        return process

    try:
        yield start
    finally:
        owned = set(roots)
        pending = list(roots)
        errors = []
        while pending:
            process = pending.pop()
            try:
                process.suspend()
            except psutil.NoSuchProcess:
                continue
            except psutil.Error as error:
                errors.append(error)
            try:
                # Freeze each generation before inspecting the next, so a
                # launcher cannot race cleanup by creating more descendants.
                children = set(process.children()) - owned
                owned.update(children)
                pending.extend(children)
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as error:
                errors.append(error)
        for process in owned:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as error:
                errors.append(error)
        for process in owned:
            try:
                process.wait(timeout=10)
            except psutil.NoSuchProcess:
                pass
            except (psutil.Error, subprocess.TimeoutExpired) as error:
                errors.append(error)
        assert not errors, f'Test process cleanup failed: {errors}'


def test_partial_launch_failure_reaps_started_children():
    with pytest.raises(FileNotFoundError):
        with _owned_processes() as start:
            child = start([sys.executable, '-c', 'import time; time.sleep(120)'])
            start([str(Path(sys.executable).parent / 'nonexistent-snipper-test.exe')])
    assert child.poll() is not None


def test_cleanup_attempts_every_child_after_one_access_error(monkeypatch):
    with pytest.raises(AssertionError, match='Test process cleanup failed'):
        with _owned_processes() as start:
            first = start([sys.executable, '-c', 'import time; time.sleep(120)'])
            second = start([sys.executable, '-c', 'import time; time.sleep(120)'])

            def denied():
                raise psutil.AccessDenied(first.pid)

            monkeypatch.setattr(first, 'suspend', denied)
    assert first.poll() is not None and second.poll() is not None


def test_cleanup_reaps_py_launcher_descendants(tmp_path):
    launcher = shutil.which('py')
    if not launcher:
        pytest.skip('Windows py launcher not installed')
    pid_file = tmp_path / 'pid'
    with _owned_processes() as start:
        root = start([launcher, '-3', '-c',
                      'import os,time,pathlib; pathlib.Path(__import__("sys").argv[1]).write_text(str(os.getpid())); time.sleep(120)',
                      str(pid_file)])
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        child_pid = int(pid_file.read_text())
        child = psutil.Process(child_pid)
        assert child in root.children(recursive=True)
    assert root.poll() is not None
    assert not child.is_running()


@pytest.mark.skipif(os.environ.get('SUPER_SNIPPER_INSTALLER_TESTS') != '1',
                    reason='set SUPER_SNIPPER_INSTALLER_TESTS=1 for real panel preflight')
def test_running_snipper_blocks_preflight_and_decoys_pass(tmp_path):
    # The real source is checked structurally; the entry point copy intercepts
    # persistent operations even if a future guard/preflight regression slips in.
    installer = _contained_copy(tmp_path)
    sleeper = tmp_path / 'super_snipper.py'
    sleeper.write_text('import time\ntime.sleep(120)\n', encoding='utf-8')
    inspector = tmp_path / 'inspect_file.py'
    inspector.write_text('import time\ntime.sleep(120)\n', encoding='utf-8')
    (tmp_path / 'sleepmod.py').write_text('import time\ntime.sleep(120)\n', encoding='utf-8')
    env = dict(os.environ, SUPER_SNIPPER_HOME=str(tmp_path / 'panel-home'),
               SUPER_SNIPPER_INSTANCE=uuid.uuid4().hex, QT_QPA_PLATFORM='offscreen')
    with _owned_processes() as start:
        blocking = {
            'panel': start([str(Path(sys.executable).with_name('pythonw.exe')), str(LAUNCHER), '--panel-service'], env=env),
            'x_option': start([sys.executable, '-X', 'utf8', str(sleeper)]),
        }
        if shutil.which('py'):
            blocking['launcher'] = start(['py', '-3', str(sleeper)])
        decoys = {
            'c_mode': start([sys.executable, '-c', 'import time; time.sleep(120)', str(sleeper)]),
            'inspector': start([sys.executable, '-W', 'ignore', str(inspector), str(sleeper)]),
            'module': start([sys.executable, '-m', 'sleepmod', str(sleeper)], cwd=tmp_path),
        }
        time.sleep(3)
        assert all(p.poll() is None for p in [*blocking.values(), *decoys.values()])
        before = _install_state()
        # Exercise the unmodified real entry point as well as the contained copy.
        # The real source passed the structural check; never mutate it here.
        for entry in (installer, INSTALLER):
            result = _run_preflight(entry, tmp_path)
            assert _install_state() == before
            output = result.stdout + result.stderr
            assert result.returncode != 0 and 'nothing was changed' in output, output
            assert 'Exit Super Snipper' in output and 'VIDEO RECORDING IS UNAVAILABLE' in output
            for process in blocking.values():
                assert f'PID {process.pid}:' in output, output
            for process in decoys.values():
                assert f'PID {process.pid}:' not in output, output
