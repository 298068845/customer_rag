#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif

#ifndef SourceDir
#define SourceDir "..\build\installer\app"
#endif

#ifndef OutputDir
#define OutputDir "..\dist\installer"
#endif

#define MyAppName "Customer RAG"
#define MyAppPublisher "Customer RAG"
#define MyAppExeName "CustomerRAG.exe"
#define MyAppId "{{7E0F7C7D-8E7E-4D22-8E9A-27ED2EFB8A7F}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Customer RAG
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=CustomerRAG-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
SetupIconFile={#SourceDir}\customer_rag\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Runtime files must be replaced as a unit. Also remove the non-portable .venv
; shipped by installer versions before the self-contained runtime migration.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\.venv"

[UninstallDelete]
; Remove logs, bytecode caches, and transient files created after installation.
; User configuration and indexes live under %LocalAppData%\CustomerRAG, outside
; {app}, and are intentionally preserved.
Type: filesandordirs; Name: "{app}"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\customer_rag\assets\app_icon.ico"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\customer_rag\assets\app_icon.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure StopCustomerRag();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM CustomerRAG.exe /T /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(
    ExpandConstant('{cmd}'),
    '/C powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { ((($_.Name -like ''python*.exe'' -or $_.Name -like ''pythonw*.exe'') -and ($_.CommandLine -like ''*customer_rag*'' -or $_.CommandLine -like ''*run_streamlit.py*'' -or $_.CommandLine -like ''*run_talk_streamlit.py*'')) -or ($_.Name -like ''AutoHotkey*.exe'' -and $_.CommandLine -like ''*WeChatQuickTool.ahk*'')) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
end;

function InitializeSetup(): Boolean;
begin
  StopCustomerRag();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopCustomerRag();
end;
