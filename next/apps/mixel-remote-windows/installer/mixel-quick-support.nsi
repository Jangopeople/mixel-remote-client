; Mixel Quick Support for Windows (2.1.6 and later): zero-admin, silent instant-launch package.
;
; Runs at user level (RequestExecutionLevel user — never prompts for UAC/Administrator),
; silently extracts the signed Mixel Remote 2.x engine & UI into %LOCALAPPDATA%\Mixel Quick Support
; (skipping extraction if the current version is already unpacked), and immediately launches
; mixel-remote.exe --quick-support.
;
;   makensis /DVERSION=2.1.6 /DSOURCE=<publish folder> /DOUTFILE=<Mixel-Quick-Support-Windows-x64.exe> mixel-quick-support.nsi

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user
SilentInstall silent
ManifestDPIAware true

!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef SOURCE
  !error "SOURCE is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "Mixel-Quick-Support-Windows-x64.exe"
!endif

!define PRODUCT "Mixel Quick Support"
!define PUBLISHER "Mixel IT & Corporate Services GmbH"

Name "${PRODUCT}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\${PRODUCT}"
BrandingText "${PRODUCT} ${VERSION}"
Icon "${SOURCE}\Assets\mixel-remote.ico"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileDescription" "${PRODUCT}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "© Mixel IT and Corporate Services"

Section "QuickSupport"
  ; Fast path: if this exact version is already unpacked, launch immediately.
  IfFileExists "$INSTDIR\mixel-remote.exe" 0 extract
  IfFileExists "$INSTDIR\qs-version.txt" 0 extract
  ClearErrors
  FileOpen $1 "$INSTDIR\qs-version.txt" r
  IfErrors extract
  FileRead $1 $2
  FileClose $1
  StrCmp $2 "${VERSION}" launch extract

extract:
  ReadEnvStr $0 USERNAME
  ; Only stop any previous Mixel Quick Support copy if we need to update its files.
  SetOutPath "$INSTDIR"
  SetOverwrite try
  File /r "${SOURCE}\*.*"
  FileOpen $1 "$INSTDIR\qs-version.txt" w
  FileWrite $1 "${VERSION}"
  FileClose $1

launch:
  Exec '"$INSTDIR\mixel-remote.exe" --quick-support'
SectionEnd
