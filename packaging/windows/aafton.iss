; Inno Setup 6 installer script for aafton desktop on Windows.

#define RepoRoot AddBackslash(SourcePath) + "..\.."
#define AppIcon RepoRoot + "\resources\brand\aafton.ico"

#ifndef AppVersion
  #define AppVersion "0.1.0a1"
#endif
#ifndef OutputDir
  #define OutputDir RepoRoot + "\dist"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "aafton-" + AppVersion + "-windows-setup"
#endif

[Setup]
AppId={{7B4E9A12-3C8D-4F6E-9A2B-1E5D8C0F4A73}
AppName=aafton
AppVersion={#AppVersion}
AppVerName=aafton {#AppVersion}
AppPublisher=Gabriel Worm
AppPublisherURL=https://github.com/ntworm/aafton
DefaultDirName={autopf}\aafton
DisableProgramGroupPage=yes
LicenseFile={#RepoRoot}\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
UninstallDisplayIcon={app}\aafton.exe
#ifexist AppIcon
SetupIconFile={#AppIcon}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#RepoRoot}\dist\aafton\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifexist AppIcon
Source: "{#AppIcon}"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Icons]
#ifexist AppIcon
Name: "{autoprograms}\aafton"; Filename: "{app}\aafton.exe"; IconFilename: "{app}\aafton.ico"
Name: "{autodesktop}\aafton"; Filename: "{app}\aafton.exe"; IconFilename: "{app}\aafton.ico"; Tasks: desktopicon
#else
Name: "{autoprograms}\aafton"; Filename: "{app}\aafton.exe"
Name: "{autodesktop}\aafton"; Filename: "{app}\aafton.exe"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\aafton.exe"; Description: "{cm:LaunchProgram,aafton}"; Flags: nowait postinstall skipifsilent
