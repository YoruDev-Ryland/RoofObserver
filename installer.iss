; RoofObserver Inno Setup Script
; Requires Inno Setup 6+

[Setup]
AppName=RoofObserver
AppVersion=1.0
AppPublisher=YoruDev
ArchitecturesInstallIn64BitMode=x64compatible
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
function InstallerServiceLogPath(): string;
begin
	Result := ExpandConstant('{app}\installer-service-setup.log');
end;

procedure AppendInstallerLog(const Message: string);
begin
	Log(Message);
	SaveStringToFile(InstallerServiceLogPath(), GetDateTimeString('yyyy-mm-dd hh:nn:ss', #0, #0) + ' ' + Message + #13#10, True);
end;

function ExecHidden(const FileName: string; const Params: string): Integer;
var
	ResultCode: Integer;
begin
	AppendInstallerLog('EXEC ' + FileName + ' ' + Params);
	if Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
	begin
		AppendInstallerLog('EXIT ' + IntToStr(ResultCode));
		Result := ResultCode
	end
	else
	begin
		AppendInstallerLog('EXEC FAILED');
		Result := -1;
	end;
end;

procedure EnsureExecHidden(const FileName: string; const Params: string; const FriendlyName: string);
var
	ResultCode: Integer;
begin
	ResultCode := ExecHidden(FileName, Params);
	if ResultCode <> 0 then
	begin
		AppendInstallerLog('FAIL ' + FriendlyName + ' (exit code ' + IntToStr(ResultCode) + ')');
		RaiseException(FriendlyName + ' failed. See ' + InstallerServiceLogPath());
	end;
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
	begin
		AppendInstallerLog('SKIP remove missing service ' + ServiceName);
		exit;
	end;

	AppendInstallerLog('Removing existing service ' + ServiceName);
	ExecHidden(NssmPath, 'stop ' + ServiceName);
	EnsureExecHidden(NssmPath, 'remove ' + ServiceName + ' confirm', 'Removing service ' + ServiceName);
end;

procedure InstallAndConfigureService(const ServiceName: string; const ExeName: string; const LogName: string);
var
	NssmPath: string;
	AppPath: string;
begin
	NssmPath := ExpandConstant('{app}\nssm.exe');
	AppPath := ExpandConstant('{app}');

	AppendInstallerLog('Installing service ' + ServiceName + ' from ' + AppPath + '\' + ExeName);
	EnsureExecHidden(NssmPath, 'install ' + ServiceName + ' "' + AppPath + '\' + ExeName + '"', 'Installing service ' + ServiceName);
	EnsureExecHidden(NssmPath, 'set ' + ServiceName + ' AppDirectory "' + AppPath + '"', 'Configuring AppDirectory for ' + ServiceName);
	EnsureExecHidden(NssmPath, 'set ' + ServiceName + ' AppStdout "' + AppPath + '\' + LogName + '"', 'Configuring stdout log for ' + ServiceName);
	EnsureExecHidden(NssmPath, 'set ' + ServiceName + ' AppStderr "' + AppPath + '\' + LogName + '"', 'Configuring stderr log for ' + ServiceName);
	EnsureExecHidden(NssmPath, 'set ' + ServiceName + ' Start SERVICE_AUTO_START', 'Configuring autostart for ' + ServiceName);
	EnsureExecHidden(NssmPath, 'set ' + ServiceName + ' AppExit Default Restart', 'Configuring restart behavior for ' + ServiceName);
	EnsureExecHidden(NssmPath, 'set ' + ServiceName + ' AppRestartDelay 5000', 'Configuring restart delay for ' + ServiceName);
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

	AppendInstallerLog('Starting post-install service configuration');

	InstallAndConfigureService('RoofObserver', 'roofobserver.exe', 'roofobserver.log');
	InstallAndConfigureService('RoofAPI', 'roofapi.exe', 'roofapi.log');

	NssmPath := ExpandConstant('{app}\nssm.exe');
	EnsureExecHidden(NssmPath, 'start RoofObserver', 'Starting service RoofObserver');
	EnsureExecHidden(NssmPath, 'start RoofAPI', 'Starting service RoofAPI');
	AppendInstallerLog('Service configuration completed successfully');
end;
