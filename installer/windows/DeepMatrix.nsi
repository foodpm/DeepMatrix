!include "MUI2.nsh"

!ifndef APP_NAME
!define APP_NAME "DeepMatrix"
!endif
!ifndef APP_VERSION
!define APP_VERSION "v0.0.0"
!endif
!ifndef SOURCE_DIR
!define SOURCE_DIR "..\\..\\dist\\DeepMatrix"
!endif
!ifndef OUTPUT_NAME
!define OUTPUT_NAME "${APP_NAME}-${APP_VERSION}-Setup.exe"
!endif

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUTPUT_NAME}"
!ifndef ICON_PATH
!define ICON_PATH "${__FILEDIR__}\..\..\packaging\assets\logo.ico"
!endif
!define MUI_ICON "${ICON_PATH}"
!define MUI_UNICON "${ICON_PATH}"
Icon "${ICON_PATH}"
UninstallIcon "${ICON_PATH}"
InstallDir "$PROGRAMFILES\\${APP_NAME}"
InstallDirRegKey HKCU "Software\\${APP_NAME}" ""
RequestExecutionLevel admin

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\\*"
  WriteRegStr HKCU "Software\\${APP_NAME}" "" "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\\${APP_NAME}\\${APP_NAME}.lnk" "$INSTDIR\\DeepMatrix.exe" "" "$INSTDIR\\DeepMatrix.exe" 0
  CreateShortCut "$DESKTOP\\${APP_NAME}.lnk" "$INSTDIR\\DeepMatrix.exe" "" "$INSTDIR\\DeepMatrix.exe" 0
  WriteUninstaller "$INSTDIR\\Uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\\${APP_NAME}\\${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\\${APP_NAME}"
  Delete "$INSTDIR\\Uninstall.exe"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\\${APP_NAME}"
SectionEnd
