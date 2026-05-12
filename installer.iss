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
var
	RoofFilePage: TInputFileWizardPage;
	ConfiguredShareRoot: string;
	ConfigShareRootPlaceholder: string;

function InstallerServiceLogPath(): string;
begin
	Result := ExpandConstant('{app}\installer-service-setup.log');
end;

function InstalledConfigPath(): string;
begin
	Result := ExpandConstant('{app}\config.json');
end;

procedure AppendInstallerLog(const Message: string);
var
	LogLine: AnsiString;
begin
	Log(Message);
	LogLine := GetDateTimeString('yyyy-mm-dd hh:nn:ss', #0, #0) + ' ' + Message + #13#10;
	SaveStringToFile(InstallerServiceLogPath(), LogLine, True);
end;

function NormalizeSlashes(const Value: string): string;
begin
	Result := Value;
	StringChangeEx(Result, '/', '\', True);
end;

function JsonEscapeBackslashes(const Value: string): string;
begin
	Result := Value;
	StringChangeEx(Result, '\', '\\', True);
end;

function DeriveShareRootFromRoofFile(const RoofFilePath: string; var ShareRoot: string): Boolean;
var
	NormalizedPath: string;
	LowerPath: string;
	MarkerPos: Integer;
begin
	Result := False;
	NormalizedPath := NormalizeSlashes(Trim(RoofFilePath));
	if CompareText(ExtractFileName(NormalizedPath), 'RoofStatusFile.txt') <> 0 then
		exit;

	LowerPath := Lowercase(NormalizedPath);
	MarkerPos := Pos('\roof\', LowerPath);
	if MarkerPos <= 1 then
		exit;

	ShareRoot := Copy(NormalizedPath, 1, MarkerPos - 1);
	if ShareRoot = '' then
		exit;

	Result := True;
end;

function ConfigHasPlaceholderOrMissing(): Boolean;
var
	RawContents: AnsiString;
	Contents: string;
begin
	if not FileExists(InstalledConfigPath()) then
	begin
		Result := True;
		exit;
	end;

	if not LoadStringFromFile(InstalledConfigPath(), RawContents) then
	begin
		Result := True;
		exit;
	end;

	Contents := RawContents;
	Result := Pos(ConfigShareRootPlaceholder, Contents) > 0;
end;

procedure UpdateInstalledConfigShareRoot(const ShareRoot: string);
var
	RawContents: AnsiString;
	Contents: string;
	EscapedShareRoot: string;
begin
	if not LoadStringFromFile(InstalledConfigPath(), RawContents) then
		RaiseException('Unable to read ' + InstalledConfigPath());
	Contents := RawContents;

	EscapedShareRoot := JsonEscapeBackslashes(ShareRoot);
	if Pos(ConfigShareRootPlaceholder, Contents) > 0 then
		StringChangeEx(Contents, ConfigShareRootPlaceholder, EscapedShareRoot, True)
	else
		RaiseException('config.json no longer contains the expected share_root placeholder. Edit config.json manually.');

	RawContents := Contents;
	if not SaveStringToFile(InstalledConfigPath(), RawContents, False) then
		RaiseException('Unable to write ' + InstalledConfigPath());

	AppendInstallerLog('Configured share_root=' + ShareRoot);
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

procedure InitializeWizard;
begin
	ConfigShareRootPlaceholder := '\\\\YOUR-SHARE-HOST\\SFROShare';
	RoofFilePage := CreateInputFilePage(
		wpSelectDir,
		'Observatory Share Location',
		'Select one real roof status file from the observatory share.',
		'Browse to a file like \\server\share\roof\building-2\RoofStatusFile.txt. The installer will derive the share root automatically.'
	);
	RoofFilePage.Add('&Roof status file:', 'Roof status file|RoofStatusFile.txt|Text files|*.txt|All files|*.*', '.txt');
	ConfiguredShareRoot := '';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
	Result := False;
	if (RoofFilePage <> nil) and (PageID = RoofFilePage.ID) then
		Result := not ConfigHasPlaceholderOrMissing();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
	ShareRoot: string;
begin
	Result := True;
	if (RoofFilePage = nil) or (CurPageID <> RoofFilePage.ID) then
		exit;

	if Trim(RoofFilePage.Values[0]) = '' then
	begin
		MsgBox('Select a RoofStatusFile.txt from your observatory share so the installer can configure share_root correctly.', mbError, MB_OK);
		Result := False;
		exit;
	end;

	if not DeriveShareRootFromRoofFile(RoofFilePage.Values[0], ShareRoot) then
	begin
		MsgBox('The selected file must be a RoofStatusFile.txt located under a \\server\share\roof\building-* path.', mbError, MB_OK);
		Result := False;
		exit;
	end;

	ConfiguredShareRoot := ShareRoot;
	AppendInstallerLog('User selected roof file ' + RoofFilePage.Values[0]);
	AppendInstallerLog('Derived share root ' + ConfiguredShareRoot);
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
	if ConfiguredShareRoot <> '' then
		UpdateInstalledConfigShareRoot(ConfiguredShareRoot)
	else if ConfigHasPlaceholderOrMissing() then
		RaiseException('share_root is still unconfigured. Re-run the installer and select a RoofStatusFile.txt file.');

	InstallAndConfigureService('RoofObserver', 'roofobserver.exe', 'roofobserver.log');
	InstallAndConfigureService('RoofAPI', 'roofapi.exe', 'roofapi.log');

	NssmPath := ExpandConstant('{app}\nssm.exe');
	EnsureExecHidden(NssmPath, 'start RoofObserver', 'Starting service RoofObserver');
	EnsureExecHidden(NssmPath, 'start RoofAPI', 'Starting service RoofAPI');
	AppendInstallerLog('Service configuration completed successfully');
end;
