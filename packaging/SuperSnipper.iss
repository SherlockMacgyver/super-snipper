; Compiled by build_release.py with explicit version/payload/output paths.
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppGuid
  #error AppGuid is required
#endif

[Setup]
AppId={#AppGuid}
AppName=Super Snipper
AppVersion={#AppVersion}
AppPublisher=Super Snipper contributors
AppPublisherURL=https://github.com/SherlockMacgyver/super-snipper
DefaultDirName={localappdata}\Programs\Super Snipper
DefaultGroupName=Super Snipper
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=SuperSnipper-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
DisableDirPage=auto
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\versions\{#AppVersion}\SuperSnipper.exe
LicenseFile={#PayloadDir}\LICENSE
SetupLogging=yes

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: startup; Description: "Start Super Snipper when I sign in"; GroupDescription: "Startup:"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Super Snipper"; Filename: "{app}\versions\{#AppVersion}\SuperSnipper.exe"; WorkingDir: "{app}\versions\{#AppVersion}"
Name: "{autodesktop}\Super Snipper"; Filename: "{app}\versions\{#AppVersion}\SuperSnipper.exe"; Tasks: desktopicon
Name: "{userstartup}\Super Snipper"; Filename: "{app}\versions\{#AppVersion}\SuperSnipper.exe"; Tasks: startup

[Registry]
Root: HKCU; Subkey: "Software\SuperSnipper"; ValueType: string; ValueName: "InstallRoot"; ValueData: "{app}"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\SuperSnipper"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\versions\{#AppVersion}\SuperSnipper.exe"; Description: "Launch Super Snipper"; Flags: nowait postinstall skipifsilent

[Code]
function RunningReason(): String;
var
  Locator, Service, Processes, Process: Variant;
  I: Integer;
  Path, Root: String;
begin
  Result := '';
  Root := AddBackslash(ExpandConstant('{app}'));
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('', 'root\CIMV2');
    Processes := Service.ExecQuery('SELECT Name, ExecutablePath, ProcessId FROM Win32_Process WHERE Name = ''SuperSnipper.exe'' OR Name = ''SuperSnipperCLI.exe'' OR Name = ''AutoHotkey64.exe''');
    for I := 0 to Processes.Count - 1 do begin
      Process := Processes.ItemIndex(I);
      if VarIsNull(Process.ExecutablePath) then begin
        Result := 'Cannot inspect a running capture process. Close Super Snipper and retry.';
        Exit;
      end;
      Path := Process.ExecutablePath;
      if CompareText(Copy(Path, 1, Length(Root)), Root) = 0 then begin
        Result := 'Super Snipper is running. Stop any recording, then choose Exit Super Snipper from its tray menu and retry. Your captures will be kept.';
        Exit;
      end;
    end;
  except
    Result := 'Could not check running applications. Nothing has been changed. ' + GetExceptionMessage;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := RunningReason();
end;

function InitializeUninstall(): Boolean;
var Reason: String;
begin
  Reason := RunningReason();
  Result := Reason = '';
  if not Result then begin
    Log(Reason);
    if not UninstallSilent then MsgBox(Reason, mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('startup') then
    DeleteFile(ExpandConstant('{userstartup}\Super Snipper.lnk'));
end;
