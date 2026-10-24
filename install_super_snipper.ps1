# Install / refresh the Super Snipper tray and Win+Shift hotkeys for the current user.
#
#   .\install_super_snipper.ps1 -Python C:\Python314\python.exe -FFmpeg C:\ffmpeg\bin\ffmpeg.exe
#
# -Python    interpreter that has the requirements installed (default: python.exe on PATH)
# -FFmpeg    ffmpeg.exe for video recording (optional when ffmpeg is already on PATH)
# -PreflightOnly runs checks without changing user settings, startup or the tray.
# The chosen interpreter and FFmpeg are saved as user environment variables
# (SUPER_SNIPPER_PYTHONW, SUPER_SNIPPER_FFMPEG) so the tray uses the same ones.

param(
    [string]$Python = "",
    [string]$AutoHotkey = (Join-Path $env:ProgramFiles "AutoHotkey\v2\AutoHotkey.exe"),
    [string]$FFmpeg = "",
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"

$AppDir = $PSScriptRoot
$Ahk = $AutoHotkey
$HotkeyScript = Join-Path $AppDir "super_snipper_hotkey.ahk"
$Snipper = Join-Path $AppDir "super_snipper.py"
$TaskName = "SuperSnipperHotkey"
$StartupShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Super Snipper Hotkey.lnk"

if (-not $Python) {
    $Python = (Get-Command python.exe -ErrorAction Stop).Source
}
$Pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"

Add-Type -Namespace SuperSnipper -Name CommandLine -MemberDefinition @'
[DllImport("shell32.dll", SetLastError = true)]
static extern System.IntPtr CommandLineToArgvW(
    [MarshalAs(UnmanagedType.LPWStr)] string cmdLine, out int numArgs);
[DllImport("kernel32.dll")]
static extern System.IntPtr LocalFree(System.IntPtr mem);
public static string[] Split(string commandLine) {
    int count;
    System.IntPtr argv = CommandLineToArgvW(commandLine, out count);
    if (argv == System.IntPtr.Zero) {
        throw new System.ComponentModel.Win32Exception();
    }
    try {
        string[] args = new string[count];
        for (int i = 0; i < count; i++) {
            args[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, i * System.IntPtr.Size));
        }
        return args;
    } finally {
        LocalFree(argv);
    }
}
'@

function Get-ExecutedPythonScript([string]$CommandLine, [string]$Image) {
    # The script CPython runs, or $null for -c, -m, - (stdin) or no script.
    # Arguments after the script belong to the script, not to Python.
    $argv = [SuperSnipper.CommandLine]::Split($CommandLine)
    $i = 1
    if ($Image -match '^pyw?\.exe$') {
        # py launcher selectors come before the interpreter's own options.
        while ($i -lt $argv.Count -and $argv[$i] -match '^-(\d+(\.\d+)*(-(32|64|arm64))?|V:.+)$') { $i++ }
    }
    while ($i -lt $argv.Count) {
        $arg = $argv[$i]
        if ($arg -eq '--') {
            if ($i + 1 -lt $argv.Count) { return $argv[$i + 1] }
            return $null
        }
        if ($arg -eq '-' -or -not $arg.StartsWith('-')) {
            if ($arg -eq '-') { return $null }
            return $arg
        }
        if ($arg.StartsWith('--')) {
            if ($arg -eq '--check-hash-based-pycs') { $i++ }
            $i++
            continue
        }
        for ($j = 1; $j -lt $arg.Length; $j++) {
            $option = $arg[$j]
            if ($option -ceq 'c' -or $option -ceq 'm') { return $null }
            if ($option -ceq 'X' -or $option -ceq 'W') {
                # Value is the rest of this argument, or the next argument.
                if ($j -eq $arg.Length - 1) { $i++ }
                break
            }
        }
        $i++
    }
    return $null
}

function Assert-NoRunningSnipper {
    # Older versions kept recording state in another folder, so installing over a
    # running panel or recorder could start a second recorder beside it. Refuse
    # before changing anything; never stop a capture to make room.
    $python = @(Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^(python|pythonw|py|pyw)[\d.]*\.exe$' -and $_.ProcessId -ne $PID })
    $running = @($python | Where-Object {
        $null -ne $_.CommandLine -and
        ($executed = Get-ExecutedPythonScript $_.CommandLine $_.Name) -and
        [IO.Path]::GetFileName($executed) -ieq 'super_snipper.py' })
    $unreadable = @($python | Where-Object { $null -eq $_.CommandLine })
    if ($running.Count -or $unreadable.Count) {
        $lines = @($running | ForEach-Object { "  PID $($_.ProcessId): $($_.CommandLine)" })
        $lines += @($unreadable | ForEach-Object {
            "  PID $($_.ProcessId): $($_.Name) (command line not readable; it may be Super Snipper)" })
        throw ("Super Snipper is still running, so it was not reinstalled and nothing was changed.`n" +
            ($lines -join "`n") + "`n" +
            "Choose Exit Super Snipper from its tray icon, wait for any recording to finish saving, " +
            "then run this installer again.")
    }
}

if (!(Test-Path -LiteralPath $Ahk)) {
    throw "AutoHotkey v2 not found: $Ahk"
}
if (!(Test-Path -LiteralPath $HotkeyScript)) {
    throw "Hotkey script not found: $HotkeyScript"
}
if (!(Test-Path -LiteralPath $Python)) {
    throw "Python not found: $Python"
}
if (!(Test-Path -LiteralPath $Pythonw)) {
    throw "pythonw.exe not found beside $Python"
}
if (!(Test-Path -LiteralPath $Snipper)) {
    throw "Super Snipper script not found: $Snipper"
}
& $Python -c "import PySide6, PIL, numpy, psutil, soundcard"
if ($LASTEXITCODE -ne 0) {
    throw "Install the requirements for $Python first: $Python -m pip install -r $(Join-Path $AppDir "super_snipper_requirements.txt")"
}

if ($FFmpeg) {
    if (!(Test-Path -LiteralPath $FFmpeg -PathType Leaf)) {
        throw "FFmpeg not found: $FFmpeg"
    }
    $env:SUPER_SNIPPER_FFMPEG = $FFmpeg
} elseif (-not $env:SUPER_SNIPPER_FFMPEG) {
    # The tray reads the saved user setting, so check with the same one.
    $env:SUPER_SNIPPER_FFMPEG = [Environment]::GetEnvironmentVariable("SUPER_SNIPPER_FFMPEG", "User")
}

# The app's own self-test resolves FFmpeg exactly as recording will.
$selfTest = @(& $Python $Snipper --self-test)
$selfTestExit = $LASTEXITCODE
$selfTest | Write-Host
if ($selfTestExit -ne 0) {
    throw "Super Snipper self-test failed"
}
$ffmpegError = $selfTest | Where-Object { "$_" -like "ffmpeg_error=*" } | Select-Object -First 1
if ($ffmpegError) {
    Write-Warning ("VIDEO RECORDING IS UNAVAILABLE: FFmpeg was not found. Snips will work. " +
        "Install FFmpeg on PATH or rerun with -FFmpeg <path to ffmpeg.exe> to enable recording.")
}

Assert-NoRunningSnipper

if ($PreflightOnly) {
    Write-Host "Preflight passed. No installation changes were made."
    exit 0
}

# Installation changes begin here. Keep all persistent operations below preflight.
function Stop-ExistingHotkeyProcess {
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -like "AutoHotkey*" } |
        Where-Object { $_.CommandLine -like "*super_snipper_hotkey.ahk*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Install-StartupShortcut {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($StartupShortcut)
    $shortcut.TargetPath = $Ahk
    $shortcut.Arguments = "`"$HotkeyScript`""
    $shortcut.WorkingDirectory = $AppDir
    $shortcut.Description = "Run the Super Snipper tray and Win+Shift hotkeys."
    $shortcut.Save()
    Write-Host "Installed startup shortcut: $StartupShortcut"
}

function Start-HotkeyProcess {
    Start-Process `
        -FilePath $Ahk `
        -ArgumentList "`"$HotkeyScript`"" `
        -WorkingDirectory $AppDir `
        -WindowStyle Hidden
    Start-Sleep -Seconds 1
}

[Environment]::SetEnvironmentVariable("SUPER_SNIPPER_PYTHONW", $Pythonw, "User")
$env:SUPER_SNIPPER_PYTHONW = $Pythonw
if ($FFmpeg) {
    [Environment]::SetEnvironmentVariable("SUPER_SNIPPER_FFMPEG", $FFmpeg, "User")
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Stop-ExistingHotkeyProcess

$action = New-ScheduledTaskAction `
    -Execute $Ahk `
    -Argument "`"$HotkeyScript`"" `
    -WorkingDirectory $AppDir

$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Run the Super Snipper tray and Win+Shift hotkeys." | Out-Null

    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 1
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
}
catch {
    Write-Warning "Task Scheduler install failed: $($_.Exception.Message)"
    Write-Warning "Falling back to the current user's Startup folder."
    Install-StartupShortcut
    Start-HotkeyProcess
}

Get-CimInstance Win32_Process |
    Where-Object { $_.Name -like "AutoHotkey*" } |
    Where-Object { $_.CommandLine -like "*super_snipper_hotkey.ahk*" } |
    Select-Object Name, ProcessId, CommandLine
