@echo off
setlocal
REM ============================================================
REM  AiConstruction one-click obfuscation script
REM  Prereq: dotnet SDK + Obfuscar.GlobalTool installed
REM  Install Obfuscar once:
REM      dotnet tool install --global Obfuscar.GlobalTool
REM ============================================================

set BASE=%~dp0

echo [1/4] Building Release (no PDB)...
dotnet build "%BASE%AiConstruction.sln" -c Release -p:DebugType=None -p:DebugSymbols=false
if errorlevel 1 ( echo BUILD FAILED & exit /b 1 )

echo [2/4] Running Obfuscar...
where obfuscar.console >nul 2>nul
if errorlevel 1 (
  "%USERPROFILE%\.dotnet\tools\obfuscar.console.exe" "%BASE%Obfuscar.xml"
) else (
  obfuscar.console "%BASE%Obfuscar.xml"
)
if errorlevel 1 ( echo OBFUSCAR FAILED & exit /b 1 )

echo [3/4] Copying obfuscated dll back to publish dir...
copy /Y "%BASE%bin\Obfuscated\AiConstruction.dll" "%BASE%bin\Release\net8.0-windows\AiConstruction.dll" >nul
if errorlevel 1 ( echo COPY FAILED & exit /b 1 )

echo [4/4] DONE. Publish dir: %BASE%bin\Release\net8.0-windows
echo.
echo Verify with dnSpy: App/View classes keep names, other classes renamed to a/b/c...
echo Keep bin\ObfuscarMapping.xml for crash-stack decoding.
pause
