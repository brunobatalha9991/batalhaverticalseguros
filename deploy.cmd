@echo off
rem Publica o CRM no Firebase Hosting (site crm-vertical-seguros). Use "deploy.cmd --regras" para publicar tambem as regras.
cd /d "%~dp0"
if exist site rmdir /s /q site
mkdir site
copy /y crmbatalha.html site\index.html >nul
copy /y crmbatalha.html site\crmbatalha.html >nul
if "%1"=="--regras" (
  firebase deploy --only hosting:crm,firestore:rules --project gc---vertical-seguros
) else (
  firebase deploy --only hosting:crm --project gc---vertical-seguros
)
