; Evolve Mod Manager — per-user Windows installer.
; App-only: never bundle the game, keys, FFDec, Java runtime or user projects.
#define AppVer GetEnv("EVOLVE_INSTALLER_VERSION")
#if AppVer == ""
  #define AppVer "1.1.17.1"
#endif
[Setup]
AppId={{AA63F2C7-92DD-4BD2-A5B0-2E7815F1D786}
AppName=Evolve Mod Manager
AppVersion={#AppVer}
AppPublisher=Evolve Mod Manager
DefaultDirName={localappdata}\EvolveModManager
DefaultGroupName=Evolve Mod Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=EvolveModManager-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\source\assets\hunt.ico
UninstallDisplayIcon={app}\EvolveModManager.exe
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
ChangesAssociations=no
DisableReadyPage=no
AllowNoIcons=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startmenuicon"; Description: "Add to Start Menu / Windows search"; GroupDescription: "Shortcuts:"

[Files]
; Explicit allowlist: build-time screenshots may generate Data next to the EXE.
; Never traverse or install Data, signing keys, PAKs or project workspaces.
Source: "..\.build\app-dist\EvolveModManager\EvolveModManager.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.build\app-dist\EvolveModManager\EvolveModWorker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.build\app-dist\EvolveModManager\BUILD_COMMIT.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.build\app-dist\EvolveModManager\BUILD_CHANNEL.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.build\app-dist\EvolveModManager\BUILD_REVISION.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.build\app-dist\EvolveModManager\START-HERE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.build\app-dist\EvolveModManager\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pak,private_key.pem,*.customkey-*,ffdec*,*.jar,java.exe,javaw.exe,javac.exe,jvm.dll,jre\\*,jdk\\*,temurin\\*,openjdk\\*"
Source: "..\.build\app-dist\EvolveModManager\Docs\*"; DestDir: "{app}\Docs"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pak,private_key.pem,*.customkey-*,ffdec*,*.jar,java.exe,javaw.exe,javac.exe,jvm.dll,jre\\*,jdk\\*,temurin\\*,openjdk\\*"

[Icons]
Name: "{autodesktop}\Evolve Mod Manager"; Filename: "{app}\EvolveModManager.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userprograms}\Evolve Mod Manager"; Filename: "{app}\EvolveModManager.exe"; WorkingDir: "{app}"; Tasks: startmenuicon

[Run]
Filename: "{app}\EvolveModManager.exe"; Description: "Launch Evolve Mod Manager"; Flags: nowait postinstall skipifsilent
