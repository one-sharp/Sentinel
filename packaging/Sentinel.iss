#define MyAppVersion "0.3.1"
#define ProjectRoot AddBackslash(SourcePath) + ".."
[Setup]
AppId={{7BE8FB91-851A-4C31-989A-F1E18777E056}
AppName=Sentinel
AppVersion={#MyAppVersion}
AppPublisher=Sentinel
DefaultDirName={localappdata}\Programs\Sentinel
DefaultGroupName=Sentinel
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ProjectRoot}\release
OutputBaseFilename=Sentinel-Setup
SetupIconFile={#ProjectRoot}\assets\sentinel-idle.ico
UninstallDisplayIcon={app}\Sentinel.exe
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\SentinelDesktop-v031
CloseApplications=no
RestartApplications=no
Uninstallable=yes
SetupLogging=yes
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
[Files]
Source: "{#ProjectRoot}\dist\Sentinel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Sentinel"; Filename: "{app}\Sentinel.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Sentinel"; Filename: "{app}\Sentinel.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\Sentinel.exe"; Description: "Launch Sentinel"; Flags: nowait postinstall skipifsilent
; Deliberately no [UninstallDelete] for LocalAppData\Sentinel (user-owned data).
