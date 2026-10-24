; Native per-user installation. Only the Relay program directory is owned.
[Setup]
AppId=CodeRelayBridge.{#Role}
AppName=CodeRelayBridge {#Role}
AppVersion={#Version}
AppPublisher=Gabriel
DefaultDirName={code:InstallDirectory}
DisableDirPage=no
DisableProgramGroupPage=yes
UsePreviousAppDir=not PayloadOnly
UsePreviousGroup=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir={#Output}
OutputBaseFilename={#OutputName}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=CodeRelayBridge {#Role} ({#Edition})
UninstallFilesDir={code:StateDirectory}
Uninstallable=not PayloadOnly
CreateUninstallRegKey=not PayloadOnly
CloseApplications=no
RestartApplications=no
SetupLogging=yes
ExtraDiskSpaceRequired={#BackupReserve}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[CustomMessages]
english.LaunchCRB=Launch CodeRelayBridge
chinesesimp.LaunchCRB=启动 CodeRelayBridge
english.QuitRelay=Please quit this Relay application and stop its Relay service, then retry. Do not close Codex or DeepSeek. Existing configuration has not been changed.
chinesesimp.QuitRelay=请先退出对应的 Relay，并停止其 Relay 服务，然后重试。无需关闭 Codex 或 DeepSeek；现有配置尚未改变。
english.UnsafePath=The installation directory is redirected. Nothing was changed.
chinesesimp.UnsafePath=安装目录存在路径重定向，未修改任何内容。
english.CleanupFailed=Startup cleanup failed. Uninstall was cancelled; user data was not removed.
chinesesimp.CleanupFailed=启动项清理失败，卸载已取消；未删除个人数据。
english.MoveExisting=This Relay is already installed in another directory. Uninstall it before choosing a new location. Pairing, settings and history are retained.
chinesesimp.MoveExisting=此 Relay 已安装在其他目录。更换位置前请先卸载现有程序；配对、设置和历史会保留。

[Files]
Source: "{#Source}\{#Product}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Source}\RELEASE.json"; DestDir: "{code:MetadataDirectory}"; Flags: ignoreversion
Source: "{#Source}\DEPENDENCIES.json"; DestDir: "{code:MetadataDirectory}"; Flags: ignoreversion
Source: "{#Source}\LICENSE*"; DestDir: "{code:MetadataDirectory}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Source}\docs\*"; DestDir: "{code:MetadataDirectory}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Source}\THIRD_PARTY_NOTICES.md"; DestDir: "{code:MetadataDirectory}"; Flags: ignoreversion
Source: "{#Source}\LICENSING.md"; DestDir: "{code:MetadataDirectory}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\CodeRelayBridge\CodeRelayBridge {#Role}"; Filename: "{app}\{#Product}.exe"; WorkingDir: "{app}"; Check: not PayloadOnly
Name: "{userdesktop}\Relay {#Role}"; Filename: "{app}\{#Product}.exe"; WorkingDir: "{app}"; Check: not PayloadOnly

[Run]
Filename: "{app}\{#Product}.exe"; Description: "{cm:LaunchCRB}"; Flags: nowait postinstall skipifsilent; Check: not PayloadOnly

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[InstallDelete]
Type: filesandordirs; Name: "{app}\*"; Check: not PayloadOnly

[Code]
var Extraction, PreviousPayload, SelectedDirectory: String; InstallBegan, InstallCommitted: Boolean;

function EnumProcesses(var First: LongWord; Bytes: LongWord; var Needed: LongWord): Boolean;
  external 'EnumProcesses@psapi.dll stdcall';
function OpenProcess(Access: LongWord; Inherit: Boolean; PID: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function QueryFullProcessImageName(Process: THandle; Flags: LongWord; Name: String; var Size: LongWord): Boolean;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function GetFileAttributes(Path: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function SystemTickCount: LongWord;
  external 'GetTickCount@kernel32.dll stdcall';

function RunningRelay(Executable: String): Boolean;
var IDs: array[0..8191] of LongWord; Needed, Index, Size: LongWord; Handle: THandle; Image: String;
begin
  Result := True;
  if not EnumProcesses(IDs[0], SizeOf(IDs), Needed) then Exit;
  if Needed >= SizeOf(IDs) then Exit;
  Result := False;
  for Index := 0 to (Needed div 4) - 1 do begin
    Handle := OpenProcess($1000, False, IDs[Index]);
    if Handle <> 0 then begin
      Size := 32768; Image := StringOfChar(#0, Size);
      if QueryFullProcessImageName(Handle, 0, Image, Size) then begin
        SetLength(Image, Size);
        if (Pos(Lowercase(AddBackslash(ExtractFileDir(Executable))), Lowercase(Image)) = 1) and
           (CompareText(Image, AddBackslash(ExtractFileDir(Executable)) + 'Uninstall.exe') <> 0) then Result := True;
      end;
      CloseHandle(Handle);
      if Result then Exit;
    end;
  end;
end;

function PayloadOnly: Boolean;
begin
  Result := ExpandConstant('{param:CRBEXTRACT|}') <> '';
end;

function NormalDirectory: String;
begin
  Result := ExpandConstant('{localappdata}\CodexRelay' + Copy('{#Product}', 6, 6) + '\app');
end;

function InstallDirectory(Param: String): String;
begin
  Extraction := ExpandFileName(ExpandConstant('{param:CRBEXTRACT|}'));
  if PayloadOnly then Result := AddBackslash(Extraction) + '{#Product}'
  else Result := NormalDirectory;
end;

function StateDirectory(Param: String): String;
begin
  if PayloadOnly then Result := AddBackslash(ExpandFileName(ExpandConstant('{param:CRBEXTRACT|}'))) + '.installer'
  else Result := ExtractFileDir(NormalDirectory) + '\installer';
end;

function MetadataDirectory(Param: String): String;
begin
  if PayloadOnly then Result := ExpandFileName(ExpandConstant('{param:CRBEXTRACT|}'))
  else Result := StateDirectory('') + '\package';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := PayloadOnly and (PageID = wpSelectDir);
end;

function SafeDirectory(Path: String): Boolean;
var Attributes: LongWord; Parent: String;
begin
  Result := False;
  Path := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  while Length(Path) > 3 do begin
    Attributes := GetFileAttributes(Path);
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then Exit;
    Parent := ExtractFileDir(Path);
    if Parent = Path then Exit;
    Path := Parent;
  end;
  Result := True;
end;

function SafeContents(Path: String): Boolean;
var Row: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Path) + '*', Row) then begin
    try
      repeat
        if (Row.Name <> '.') and (Row.Name <> '..') then begin
          if (Row.Attributes and $400) <> 0 then begin Result := False; Exit; end;
          if (Row.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            if not SafeContents(AddBackslash(Path) + Row.Name) then begin Result := False; Exit; end;
        end;
      until not FindNext(Row);
    finally FindClose(Row); end;
  end;
end;

function OwnedApplication(Path: String): Boolean;
var Identity: AnsiString; Text: String; Location: String;
begin
  Result := False;
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\CodeRelayBridge.{#Role}_is1', 'InstallLocation', Location) then
    if CompareText(RemoveBackslashUnlessRoot(Location), Path) = 0 then begin Result := True; Exit; end;
  if not FileExists(Path + '\{#Product}.exe') then Exit;
  if not LoadStringFromFile(Path + '\_internal\relay-edition.json', Identity) then Exit;
  if Length(Identity) > 4096 then Exit;
  Text := String(Identity);
  StringChangeEx(Text, ' ', '', True); StringChangeEx(Text, #9, '', True);
  StringChangeEx(Text, #10, '', True); StringChangeEx(Text, #13, '', True);
  Result := Pos('"role":"{#Role}"', Text) <> 0;
end;

function EmptyDirectory(Path: String): Boolean;
var Row: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Path) + '*', Row) then begin
    try repeat
      if (Row.Name <> '.') and (Row.Name <> '..') then begin Result := False; Exit; end;
    until not FindNext(Row); finally FindClose(Row); end;
  end;
end;

function CopyPayload(Source, Destination: String): Boolean;
var Row: TFindRec; FromPath, ToPath: String;
begin
  Result := False;
  if not ForceDirectories(Destination) then Exit;
  if FindFirst(AddBackslash(Source) + '*', Row) then begin
    try repeat
      if (Row.Name <> '.') and (Row.Name <> '..') then begin
        if (Row.Attributes and $400) <> 0 then Exit;
        FromPath := AddBackslash(Source) + Row.Name; ToPath := AddBackslash(Destination) + Row.Name;
        if (Row.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
          if not CopyPayload(FromPath, ToPath) then Exit;
        end else begin
          if not FileCopy(FromPath, ToPath, False) then Exit;
          if GetSHA256OfFile(FromPath) <> GetSHA256OfFile(ToPath) then Exit;
        end;
      end;
    until not FindNext(Row); finally FindClose(Row); end;
  end;
  Result := True;
end;

function InitializeSetup: Boolean;
var Root: String;
begin
  if PayloadOnly then begin
    Root := ExpandFileName(ExpandConstant('{param:CRBEXTRACT|}'));
    Result := WizardSilent and not DirExists(Root) and SafeDirectory(Root);
    if not Result then SuppressibleMsgBox('CRB: extraction requires a new, non-redirected directory and silent mode.', mbError, MB_OK, IDOK);
  end else begin
    Result := True;
    if not Result then SuppressibleMsgBox(CustomMessage('UnsafePath'), mbError, MB_OK, IDOK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Existing, StateRoot, OtherStateRoot, Path: String;
begin
  Result := '';
  Path := RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}')));
  Log('CRB application directory: ' + ExpandConstant('{app}'));
  Log('CRB expected directory: ' + InstallDirectory(''));
  if PayloadOnly then begin
    if CompareText(Path, RemoveBackslashUnlessRoot(InstallDirectory(''))) <> 0 then
      Result := CustomMessage('UnsafePath');
    Exit;
  end;
  StateRoot := ExtractFileDir(NormalDirectory);
  OtherStateRoot := ExpandConstant('{localappdata}\CodexRelayClient');
  if CompareText(StateRoot, OtherStateRoot) = 0 then OtherStateRoot := ExpandConstant('{localappdata}\CodexRelayServer');
  if (Length(Path) <= 3) or
     (Pos(Lowercase(AddBackslash(Path)), Lowercase(AddBackslash(StateRoot))) = 1) or
     (Pos(Lowercase(AddBackslash(Path)), Lowercase(AddBackslash(OtherStateRoot))) = 1) or
     ((Pos(Lowercase(AddBackslash(StateRoot)), Lowercase(AddBackslash(Path))) = 1) and (CompareText(Path, NormalDirectory) <> 0)) or
     (Pos(Lowercase(AddBackslash(OtherStateRoot)), Lowercase(AddBackslash(Path))) = 1) then begin
    Result := CustomMessage('UnsafePath'); Exit;
  end;
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\CodeRelayBridge.{#Role}_is1', 'InstallLocation', Existing) then
    if (CompareText(RemoveBackslashUnlessRoot(Existing), Path) <> 0) and FileExists(AddBackslash(Existing) + '{#Product}.exe') then begin
      Result := CustomMessage('MoveExisting'); Exit;
    end;
  if not SafeDirectory(Path) or not SafeContents(Path) or not SafeDirectory(StateDirectory('')) or not SafeContents(StateDirectory('')) then begin Result := CustomMessage('UnsafePath'); Exit; end;
  if DirExists(Path) and not EmptyDirectory(Path) and not OwnedApplication(Path) then begin
    Result := 'CRB: an unrecognized installation occupies the application directory. Nothing was overwritten.'; Exit;
  end;
  if RunningRelay(Path + '\{#Product}.exe') then
    Result := CustomMessage('QuitRelay');
  if Result <> '' then Exit;
  SelectedDirectory := Path;
  if (PreviousPayload = '') and DirExists(Path) and not EmptyDirectory(Path) then begin
    PreviousPayload := StateDirectory('') + '\backups\app-' + GetDateTimeString('yyyymmddhhnnss', '-', ':') + '-' + IntToStr(SystemTickCount);
    if DirExists(PreviousPayload) or not CopyPayload(Path, PreviousPayload) then begin
      PreviousPayload := ''; Result := 'CRB: backup could not complete. Existing application was not replaced.';
    end;
  end;
end;

procedure CurStepChanged(Step: TSetupStep);
begin
  if PayloadOnly then Exit;
  if Step = ssInstall then InstallBegan := True;
  if Step = ssPostInstall then InstallCommitted := True;
end;

procedure DeinitializeSetup;
begin
  if (PreviousPayload <> '') and InstallBegan and not InstallCommitted then begin
    if SafeDirectory(SelectedDirectory) and SafeContents(SelectedDirectory) and SafeDirectory(PreviousPayload) and SafeContents(PreviousPayload) then begin
      if DelTree(AddBackslash(SelectedDirectory) + '*', True, True, True) and CopyPayload(PreviousPayload, SelectedDirectory) then
        Log('CRB: restored the previous application payload after interrupted installation.')
      else Log('CRB: automatic restore was incomplete; the verified backup is retained at ' + PreviousPayload);
    end;
  end;
end;

function InitializeUninstall: Boolean;
var Code: Integer; Report: String;
begin
  Result := SafeDirectory(ExpandConstant('{app}')) and SafeContents(ExpandConstant('{app}')) and OwnedApplication(RemoveBackslashUnlessRoot(ExpandConstant('{app}')));
  if not Result then Exit;
  Result := not RunningRelay(ExpandConstant('{app}\{#Product}.exe'));
  if not Result then SuppressibleMsgBox(CustomMessage('QuitRelay'), mbError, MB_OK, IDOK);
  if not Result then Exit;
  Report := ExpandConstant('{tmp}\crb-uninstall-{#Role}.json');
  Result := Exec(ExpandConstant('{app}\{#Product}.exe'), '--installer-uninstall "' + Report + '"', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  if not Result then SuppressibleMsgBox(CustomMessage('CleanupFailed'), mbError, MB_OK, IDOK);
end;
