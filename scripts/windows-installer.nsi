Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
!define VERSION "0.3.1"
!endif
!ifndef APPDIR
!define APPDIR "..\dist\release-${VERSION}\StreamCut"
!endif
!ifndef OUTPUT
!define OUTPUT "..\release\StreamCut-Setup-${VERSION}-windows-x64.exe"
!endif

Name "StreamCut"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\StreamCut"
InstallDirRegKey HKCU "Software\StreamCut\Installer" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
SetDatablockOptimize on
ManifestDPIAware true
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1049 "ProductName" "StreamCut"
VIAddVersionKey /LANG=1049 "FileDescription" "Установка StreamCut — локальный видеоредактор"
VIAddVersionKey /LANG=1049 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "LegalCopyright" "StreamCut contributors"
!define MUI_ICON "..\assets\streamcut.ico"
!define MUI_UNICON "..\assets\streamcut.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "StreamCut — локальный видеоредактор и поиск моментов.$\r$\n$\r$\nПрограмма устанавливается для вашей учётной записи без прав администратора.$\r$\n$\r$\nПосле установки откроется мастер подготовки Ollama, Whisper и Qwen. Интернет требуется для первой загрузки моделей. NVIDIA определяется автоматически; без неё работает CPU.$\r$\n$\r$\nПеред обновлением закройте StreamCut. Проекты и модели сохраняются отдельно от программы."
!insertmacro MUI_PAGE_WELCOME
!if /FileExists "..\LICENSE"
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!else
!insertmacro MUI_PAGE_LICENSE "..\THIRD_PARTY.md"
!endif
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\StreamCut.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "--setup"
!define MUI_FINISHPAGE_RUN_TEXT "Запустить StreamCut и подготовить нейросети"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Для StreamCut нужна 64-разрядная Windows."
    Abort
  ${EndIf}
  SetShellVarContext current
  SetRegView 64
FunctionEnd

Section "StreamCut" SEC_MAIN
  SetOutPath "$INSTDIR"
  File /r "${APPDIR}\*.*"
  File "..\README.md"
  File "..\THIRD_PARTY.md"
  !if /FileExists "..\LICENSE"
  File "..\LICENSE"
  !endif
  FileOpen $0 "$INSTDIR\.streamcut-installed" w
  FileWrite $0 "StreamCut ${VERSION}"
  FileClose $0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\StreamCut"
  CreateShortcut "$SMPROGRAMS\StreamCut\StreamCut.lnk" "$INSTDIR\StreamCut.exe"
  CreateShortcut "$DESKTOP\StreamCut.lnk" "$INSTDIR\StreamCut.exe"
  WriteRegStr HKCU "Software\StreamCut\Installer" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut" "DisplayName" "StreamCut"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut" "DisplayIcon" "$INSTDIR\StreamCut.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut" "NoRepair" 1
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  IfFileExists "$INSTDIR\.streamcut-installed" +3
    MessageBox MB_ICONSTOP "Не найдена отметка установки StreamCut. Удаление отменено."
    Abort
FunctionEnd

Section "Uninstall"
  ; Only installed program files are removed. User projects, media, models,
  ; exports and the independently installed Ollama are deliberately retained.
  Delete "$DESKTOP\StreamCut.lnk"
  Delete "$SMPROGRAMS\StreamCut\StreamCut.lnk"
  RMDir "$SMPROGRAMS\StreamCut"
  Delete "$INSTDIR\StreamCut.exe"
  Delete "$INSTDIR\StreamCut-worker.exe"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\THIRD_PARTY.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\.streamcut-installed"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamCut"
  DeleteRegKey HKCU "Software\StreamCut\Installer"
SectionEnd
