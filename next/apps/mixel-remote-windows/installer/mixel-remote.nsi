; Mixel Remote for Windows (2.0 and later): per-user installer.
;
; It installs where Mixel Remote 0.1 lived (%LOCALAPPDATA%\Mixel Remote, program mixel-remote.exe, the
; same "Mixel Remote" entry in Apps & features), so installing it updates 0.1 in place: the device key,
; the remembered sign-in, start with Windows and the always-on service stay as they are. Silent with /S
; (used by the in-app update and the deploy-link command).
;
;   makensis /DVERSION=2.0.0 /DSOURCE=<publish folder> /DOUTFILE=<setup.exe> mixel-remote.nsi

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user
ManifestDPIAware true

!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef SOURCE
  !error "SOURCE is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "Mixel-Remote-Windows-x64.exe"
!endif

!define PRODUCT "Mixel Remote"
!define PUBLISHER "Mixel IT & Corporate Services GmbH"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT}"
!define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"

Name "${PRODUCT}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\${PRODUCT}"
BrandingText "${PRODUCT} ${VERSION}"
Icon "${SOURCE}\Assets\mixel-remote.ico"
UninstallIcon "${SOURCE}\Assets\mixel-remote.ico"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileDescription" "${PRODUCT} setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "© Mixel IT and Corporate Services"

!include "MUI2.nsh"
!define MUI_ICON "${SOURCE}\Assets\mixel-remote.ico"
!define MUI_UNICON "${SOURCE}\Assets\mixel-remote.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\mixel-remote.exe"
!define MUI_FINISHPAGE_RUN_TEXT "$(StartNow)"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "German"
!insertmacro MUI_LANGUAGE "French"
!insertmacro MUI_LANGUAGE "Italian"
LangString StartNow ${LANG_ENGLISH} "Start Mixel Remote"
LangString StartNow ${LANG_GERMAN} "Mixel Remote starten"
LangString StartNow ${LANG_FRENCH} "Démarrer Mixel Remote"
LangString StartNow ${LANG_ITALIAN} "Avvia Mixel Remote"

; Mixel Remote of this user (0.1 or 2.x) is closed before its files are replaced.
!macro CloseApp
  ReadEnvStr $0 USERNAME
  nsExec::Exec 'taskkill /F /T /FI "USERNAME eq $0" /IM mixel-remote.exe'
  Sleep 1500
!macroend

Section "Install"
  !insertmacro CloseApp
  ; The previous version's files go first (0.1: one program; 2.x: the app folder), so no stale library
  ; stays next to the new ones. Only a folder that holds Mixel Remote is emptied.
  IfFileExists "$INSTDIR\mixel-remote.exe" 0 +2
    RMDir /r "$INSTDIR"
  SetOutPath "$INSTDIR"
  SetOverwrite on
  File /r "${SOURCE}\*.*"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\mixel-remote.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "https://store.mixel.ch/app/mixel-remote"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  ; Where 0.1 recorded its folder (its installer reads it again; harmless for 2.x).
  WriteRegStr HKCU "Software\${PUBLISHER}\${PRODUCT}" "" "$INSTDIR"
  CreateShortcut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\mixel-remote.exe" "" "$INSTDIR\mixel-remote.exe" 0
SectionEnd

Section "Uninstall"
  !insertmacro CloseApp
  DeleteRegValue HKCU "${RUN_KEY}" "${PRODUCT}"
  Delete "$SMPROGRAMS\${PRODUCT}.lnk"
  Delete "$DESKTOP\${PRODUCT}.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "Software\${PUBLISHER}\${PRODUCT}"
SectionEnd
