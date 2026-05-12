; RoofObserver Inno Setup Script
; Requires Inno Setup 6+

[Setup]
AppName=RoofObserver
AppVersion=1.0
AppPublisher=YoruDev
DefaultDirName={autopf}\RoofObserver
DefaultGroupName=RoofObserver
OutputDir=Output
OutputBaseFilename=RoofObserverSetup
Compression=lzma
SolidCompression=yes
; Require elevation so NSSM can install services
PrivilegesRequired=admin

[Files]
Source: "dist\roofobserver.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\roofapi.exe";      DestDir: "{app}"; Flags: ignoreversion
Source: "nssm.exe";               DestDir: "{app}"; Flags: ignoreversion
; Only write config.json if not already present (preserve user edits on upgrades)
Source: "config.json";            DestDir: "{app}"; Flags: onlyifdoesntexist

[Run]
; Install and configure RoofObserver (poller) service
Filename: "{app}\nssm.exe"; Parameters: "install RoofObserver ""{app}\roofobserver.exe"""; Flags: runhidden; StatusMsg: "Installing RoofObserver service..."
Filename: "{app}\nssm.exe"; Parameters: "set RoofObserver AppDirectory ""{app}"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofObserver AppStdout ""{app}\roofobserver.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofObserver AppStderr ""{app}\roofobserver.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofObserver Start SERVICE_AUTO_START"; Flags: runhidden

; Install and configure RoofAPI (API server) service
Filename: "{app}\nssm.exe"; Parameters: "install RoofAPI ""{app}\roofapi.exe"""; Flags: runhidden; StatusMsg: "Installing RoofAPI service..."
Filename: "{app}\nssm.exe"; Parameters: "set RoofAPI AppDirectory ""{app}"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofAPI AppStdout ""{app}\roofapi.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofAPI AppStderr ""{app}\roofapi.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set RoofAPI Start SERVICE_AUTO_START"; Flags: runhidden

; Start both services
Filename: "{app}\nssm.exe"; Parameters: "start RoofObserver"; Flags: runhidden; StatusMsg: "Starting services..."
Filename: "{app}\nssm.exe"; Parameters: "start RoofAPI"; Flags: runhidden

[UninstallRun]
; Stop and remove services on uninstall
Filename: "{app}\nssm.exe"; Parameters: "stop RoofObserver"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove RoofObserver confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop RoofAPI"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove RoofAPI confirm"; Flags: runhidden
