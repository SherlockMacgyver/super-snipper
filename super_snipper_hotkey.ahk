#Requires AutoHotkey v2.0
#SingleInstance Force
#UseHook True
Persistent

; install_super_snipper.ps1 records the interpreter that has the dependencies.
; Without it, pythonw.exe from PATH is used and import errors are shown visibly.
pythonw := EnvGet("SUPER_SNIPPER_PYTHONW")
if (pythonw = "")
    pythonw := "pythonw.exe"
script := A_ScriptDir "\super_snipper.py"
workdir := A_ScriptDir
exiting := false

; One table owns both bindings and displayed shortcut labels.
actions := [
    {key: "s", label: "Snip a region", command: "snip"},
    {key: "v", label: "Record / stop region", command: "region"},
    {key: "c", label: "Record / stop region (on click)", command: "arm-region"},
    {key: "x", label: "Show panel", command: "show"},
    {key: "f", label: "Record / stop fullscreen", command: "full"}
]

A_IconTip := "Super Snipper - double-click to show panel"
A_TrayMenu.Delete()
A_TrayMenu.Add("Show panel", SendCommand.Bind("show"))
A_TrayMenu.Add("Hide panel", SendCommand.Bind("hide"))
A_TrayMenu.Default := "Show panel"
A_TrayMenu.ClickCount := 2
A_TrayMenu.Add("Reset position", SendCommand.Bind("reset"))
dockMenu := Menu()
for edge in ["Left", "Right", "Top", "Bottom"]
    dockMenu.Add(edge, SendCommand.Bind("dock-" StrLower(edge)))
dockMenu.Add("Undock", SendCommand.Bind("undock"))
A_TrayMenu.Add("Dock panel", dockMenu)
A_TrayMenu.Add()
shortcutMenu := Menu()
for action in actions {
    Hotkey("#+" action.key, SendCommand.Bind(action.command))
    shortcutMenu.Add("Win+Shift+" StrUpper(action.key) " - " action.label, SendCommand.Bind(action.command))
    if action.command != "show"
        A_TrayMenu.Add(action.label, SendCommand.Bind(action.command))
}
A_TrayMenu.Add("Stop / cancel capture", SendCommand.Bind("stop"))
A_TrayMenu.Add("Keyboard shortcuts", shortcutMenu)
A_TrayMenu.Add()
A_TrayMenu.Add("Exit Super Snipper", ExitSnipper)

SendCommand(command, *) {
    global pythonw, script, workdir, exiting
    if exiting && command != "exit"
        return 1
    try {
        return RunWait('"' pythonw '" "' script '" --panel-command ' command, workdir, "Hide")
    } catch as err {
        MsgBox("Super Snipper could not launch:`n" err.Message, "Super Snipper error", "Iconx")
        return 1
    }
}

ExitSnipper(*) {
    global exiting
    if exiting
        return
    exiting := true
    ; Wait for the panel to stop/finalize recording and acknowledge shutdown.
    ; A failed command leaves the tray available and the Python error visible.
    result := SendCommand("exit")
    if result = 0 {
        ExitApp()
    }
    exiting := false
}
