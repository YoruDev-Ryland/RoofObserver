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

[UninstallRun]
; Stop and remove services on uninstall
Filename: "{app}\nssm.exe"; Parameters: "stop RoofObserver"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove RoofObserver confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop RoofAPI"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove RoofAPI confirm"; Flags: runhidden

[Code]
function ExecHidden(const FileName: string; const Params: string): Integer;
var
	ResultCode: Integer;
begin
	if Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
		Result := ResultCode
	else
		Result := -1;
end;

function ServiceExists(const ServiceName: string): Boolean;
begin
	Result := ExecHidden(ExpandConstant('{cmd}'), '/C sc query "' + ServiceName + '" >NUL 2>&1') = 0;
end;

procedure StopAndRemoveService(const ServiceName: string);
var
	NssmPath: string;
begin
	NssmPath := ExpandConstant('{app}\nssm.exe');
	if not ServiceExists(ServiceName) then
		exit;

	ExecHidden(NssmPath, 'stop ' + ServiceName);
	ExecHidden(NssmPath, 'remove ' + ServiceName + ' confirm');
end;

procedure InstallAndConfigureService(const ServiceName: string; const ExeName: string; const LogName: string);
var
	NssmPath: string;
	AppPath: string;
begin
	NssmPath := ExpandConstant('{app}\nssm.exe');
	AppPath := ExpandConstant('{app}');

	ExecHidden(NssmPath, 'install ' + ServiceName + ' "' + AppPath + '\' + ExeName + '"');
	ExecHidden(NssmPath, 'set ' + ServiceName + ' AppDirectory "' + AppPath + '"');
	ExecHidden(NssmPath, 'set ' + ServiceName + ' AppStdout "' + AppPath + '\' + LogName + '"');
	ExecHidden(NssmPath, 'set ' + ServiceName + ' AppStderr "' + AppPath + '\' + LogName + '"');
	ExecHidden(NssmPath, 'set ' + ServiceName + ' Start SERVICE_AUTO_START');
	ExecHidden(NssmPath, 'set ' + ServiceName + ' AppExit Default Restart');
	ExecHidden(NssmPath, 'set ' + ServiceName + ' AppRestartDelay 5000');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
	StopAndRemoveService('RoofObserver');
	StopAndRemoveService('RoofAPI');
	Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
	NssmPath: string;
begin
	if CurStep <> ssPostInstall then
		exit;

	InstallAndConfigureService('RoofObserver', 'roofobserver.exe', 'roofobserver.log');
	InstallAndConfigureService('RoofAPI', 'roofapi.exe', 'roofapi.log');

	NssmPath := ExpandConstant('{app}\nssm.exe');
	ExecHidden(NssmPath, 'start RoofObserver');
	ExecHidden(NssmPath, 'start RoofAPI');
end;
