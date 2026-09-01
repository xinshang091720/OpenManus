[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$Python = "python",
    [switch]$IncludeBrowser,
    [switch]$Obfuscate
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OutputRoot = Join-Path $Root "release\out\$Version"
$BuildRoot = Join-Path $Root "release\build\$Version"

# A Runtime version is immutable. Reusing a partially generated --onedir
# directory can leave DLLs open (or scanned by security software) and produce
# a corrupt mixed build. Publish a new version instead of overwriting it.
if (Test-Path -LiteralPath $OutputRoot) {
    throw "Release output already exists: $OutputRoot. Use a new -Version, or remove the incomplete output only after verifying it is not in use."
}

$PythonVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $PythonVersion -ne "3.12") {
    throw "Python 3.12 is required for the Runtime build; got '$PythonVersion'."
}

$LockFile = Join-Path $Root "requirements-runtime.lock"
if (-not (Test-Path $LockFile)) {
    & (Join-Path $PSScriptRoot "lock_runtime.ps1") -Python $Python
}
& $Python -m pip install --upgrade pip
& $Python -m pip install -r $LockFile -r (Join-Path $Root "requirements-build.txt")

if ($Obfuscate) {
    # Hybrid protected build: compile only first-party packages to native
    # extension modules. PyInstaller continues to collect third-party DLLs
    # and packages, avoiding an expensive recompilation of SciPy and NumPy.
    $NativeBuildRoot = Join-Path $BuildRoot "nuitka-native"
    $PackageRoot = Join-Path $BuildRoot "protected-src"
    New-Item -ItemType Directory -Force -Path $NativeBuildRoot, $PackageRoot | Out-Null

    & $Python -m nuitka --module app --include-package=app --nofollow-imports --output-dir=$NativeBuildRoot
    if ($LASTEXITCODE -ne 0) { throw "Nuitka application-module compilation failed." }
    & $Python -m nuitka --module q_agent_function_module\ohresult --include-package=q_agent_function_module.ohresult --nofollow-imports --output-dir=$NativeBuildRoot
    if ($LASTEXITCODE -ne 0) { throw "Nuitka CAD/IFC-module compilation failed." }

    Copy-Item -LiteralPath (Join-Path $Root "config") -Destination (Join-Path $PackageRoot "config") -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $Root "skills") -Destination (Join-Path $PackageRoot "skills") -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $Root "run_agent_runtime.py") -Destination (Join-Path $PackageRoot "run_agent_runtime.py") -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "q_agent_function_module") | Out-Null
    Copy-Item -LiteralPath (Join-Path $NativeBuildRoot "app.cp312-win_amd64.pyd") -Destination (Join-Path $PackageRoot "app.pyd") -Force
    Copy-Item -LiteralPath (Join-Path $NativeBuildRoot "ohresult.cp312-win_amd64.pyd") -Destination (Join-Path $PackageRoot "q_agent_function_module\ohresult.pyd") -Force

    # This compatibility script is deliberately loaded by its existing
    # hyphenated file path in project_delivery.py. Keep that behavior intact
    # rather than changing application logic solely for packaging.
    $LegacyIfcScriptRelativePath = "q_agent_function_module\ohresult\CAD_Git_Coordinates\my_code\ifc_function\ifc_SZ-IFC_to_docx.py"
    $LegacyIfcScriptDestination = Split-Path -Parent $LegacyIfcScriptRelativePath
    $LegacyIfcScript = Join-Path $Root $LegacyIfcScriptRelativePath
    $LegacyIfcScriptStagingDirectory = Join-Path $PackageRoot $LegacyIfcScriptDestination
    New-Item -ItemType Directory -Force -Path $LegacyIfcScriptStagingDirectory | Out-Null
    Copy-Item -LiteralPath $LegacyIfcScript -Destination (Join-Path $LegacyIfcScriptStagingDirectory "ifc_SZ-IFC_to_docx.py") -Force
}

if (-not $Obfuscate) { $PackageRoot = $Root }

# Nuitka turns imports inside app.pyd and ohresult.pyd into native code, so
# PyInstaller cannot discover these dependencies by scanning our source. Keep
# this list in one place and collect the complete package (submodules, data,
# and native DLLs) for protected builds.
$NativeRuntimePackages = @(
    "baidusearch",
    "boto3",
    "bs4",
    "dotenv",
    "duckduckgo_search",
    "ezdxf",
    "fastapi",
    "googlesearch",
    "httpx",
    "loguru",
    "numpy",
    "olefile",
    "openai",
    "pandas",
    "psutil",
    "pyautogui",
    "pydantic",
    "pydantic_core",
    "pymysql",
    "pypdf",
    "pdfplumber",
    "pywinauto",
    "reportlab",
    "requests",
    "structlog",
    "tenacity",
    "tiktoken",
    "uvicorn",
    "yaml"
)

New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null

$VersionRawParts = ($Version -split '[^\d]+') | Where-Object { $_ -ne "" }
$V0 = if ($VersionRawParts.Count -gt 0) { [int]$VersionRawParts[0] } else { 1 }
$V1 = if ($VersionRawParts.Count -gt 1) { [int]$VersionRawParts[1] } else { 0 }
$V2 = if ($VersionRawParts.Count -gt 2) { [int]$VersionRawParts[2] } else { 0 }
$V3 = if ($VersionRawParts.Count -gt 3) { [int]$VersionRawParts[3] } else { 0 }
$VersionTuple = "($V0, $V1, $V2, $V3)"

$VersionFileContent = @"
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=$VersionTuple,
    prodvers=$VersionTuple,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404b0',
          [
            StringStruct('CompanyName', 'Anbi'),
            StringStruct('FileDescription', 'BeeSync Agent Runtime'),
            StringStruct('FileVersion', '$Version'),
            StringStruct('InternalName', 'BeeSync.AgentRuntime.exe'),
            StringStruct('LegalCopyright', 'Copyright (C) 2026 Anbi. All rights reserved.'),
            StringStruct('OriginalFilename', 'BeeSync.AgentRuntime.exe'),
            StringStruct('ProductName', 'BeeSync'),
            StringStruct('ProductVersion', '$Version')
          ]
        ),
        StringTable(
          '040904b0',
          [
            StringStruct('CompanyName', 'Anbi'),
            StringStruct('FileDescription', 'BeeSync Agent Runtime'),
            StringStruct('FileVersion', '$Version'),
            StringStruct('InternalName', 'BeeSync.AgentRuntime.exe'),
            StringStruct('LegalCopyright', 'Copyright (C) 2026 Anbi. All rights reserved.'),
            StringStruct('OriginalFilename', 'BeeSync.AgentRuntime.exe'),
            StringStruct('ProductName', 'BeeSync'),
            StringStruct('ProductVersion', '$Version')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [2052, 1200, 1033, 1200])])
  ]
)
"@

$VersionFilePath = Join-Path $BuildRoot "file_version_info.txt"
[System.IO.File]::WriteAllText($VersionFilePath, $VersionFileContent, [System.Text.Encoding]::UTF8)

$PyInstallerArgs = @(
    "--noconfirm", "--clean", "--onedir",
    "--name", "BeeSync.AgentRuntime",
    "--distpath", $OutputRoot,
    "--workpath", $BuildRoot,
    "--specpath", $BuildRoot,
    "--version-file", $VersionFilePath,
    "--additional-hooks-dir", (Join-Path $PSScriptRoot "pyinstaller_hooks"),
    "--paths", $PackageRoot,
    "--add-data", "$PackageRoot\skills;skills",
    "--add-data", "$PackageRoot\config\config.toml;config",
    "--add-data", "$PackageRoot\config\config.example.toml;config",
    "--add-data", "$PackageRoot\config\mcp.example.json;config",
    "--add-data", "$PackageRoot\config\revit-plugin.example.json;config",
    "--hidden-import", "pythoncom",
    "--hidden-import", "mcp",
    "--hidden-import", "pywintypes",
    "--hidden-import", "win32com.client",
    "--hidden-import", "win32com.client.dynamic",
    "--hidden-import", "win32gui",
    "--hidden-import", "win32con",
    "--hidden-import", "win32process",
    "--hidden-import", "win32api"
)

if ($Obfuscate) {
    # app.pyd and ohresult.pyd contain the importable first-party program
    # modules. The legacy IFC script below stays as a compatibility file
    # because the healthy application loads it by its existing path.
    $PyInstallerArgs += @(
        "--add-data", "$PackageRoot\$LegacyIfcScriptRelativePath;$LegacyIfcScriptDestination",
        "--add-binary", "$PackageRoot\app.pyd;.",
        "--add-binary", "$PackageRoot\q_agent_function_module\ohresult.pyd;q_agent_function_module"
    )
    foreach ($Package in $NativeRuntimePackages) {
        $PyInstallerArgs += @("--collect-all", $Package)
    }
}
else {
    # The non-protected Runtime keeps the previous PyInstaller behavior with
    # explicit collection for tool dependencies loaded dynamically at runtime.
    $PyInstallerArgs += @(
        "--add-data", "$PackageRoot\q_agent_function_module;q_agent_function_module",
        "--hidden-import", "app.tool.base",
        "--hidden-import", "app.tool.activate_skill",
        "--hidden-import", "app.tool.create_chat_completion",
        "--hidden-import", "app.tool.terminate",
        "--hidden-import", "app.tool.tool_collection",
        "--hidden-import", "app.tool.skill_script",
        "--hidden-import", "app.workflows.revit_ifc_assignment",
        "--hidden-import", "app.revit.room_sync",
        "--hidden-import", "app.revit.room_creation",
        "--hidden-import", "app.revit.project_delivery",
        "--hidden-import", "q_agent_function_module.ohresult.logger_config",
        "--hidden-import", "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.coordinate_transformation",
        "--hidden-import", "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor",
        "--hidden-import", "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.dwg_room_extractor_main",
        "--hidden-import", "q_agent_function_module.ohresult.CAD_Git_Coordinates.my_code.room_coordinates.git_cad_floor",
        # These search/database adapters are imported by optional tools at
        # runtime, so PyInstaller cannot infer them from the entry point.
        # Keep them explicit to make --dependency-diagnose meaningful for
        # the non-protected Runtime as well.
        "--collect-all", "baidusearch",
        "--collect-all", "ezdxf",
        "--collect-all", "googlesearch",
        "--collect-all", "pymysql",
        "--collect-all", "pyautogui",
        "--collect-all", "pywinauto",
        "--collect-submodules", "pypdf",
        "--collect-submodules", "pdfplumber",
        "--collect-submodules", "reportlab"
    )
}

if ($IncludeBrowser) {
    # Store Chromium beside Playwright so PyInstaller can collect it instead
    # of relying on a developer-machine cache.
    $env:PLAYWRIGHT_BROWSERS_PATH = "0"
    & $Python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium installation failed." }
    $PyInstallerArgs += @(
        "--hidden-import", "app.tool.browser_use_tool",
        "--hidden-import", "app.tool.crawl4ai",
        "--collect-all", "browser_use",
        "--collect-all", "crawl4ai",
        "--collect-all", "markdownify",
        "--collect-all", "playwright"
    )
}

$PyInstallerArgs += (Join-Path $PackageRoot "run_agent_runtime.py")
& $Python -m PyInstaller @PyInstallerArgs

if ($LASTEXITCODE -ne 0) { throw "PyInstaller packaging failed." }

$RuntimeExe = Join-Path $OutputRoot "BeeSync.AgentRuntime\BeeSync.AgentRuntime.exe"
$SmokeData = Join-Path $BuildRoot "doctor-data"

if ($Obfuscate) {
    $InternalRuntimeRoot = Join-Path (Split-Path -Parent $RuntimeExe) "_internal"
    $NativeFirstPartyModules = @(
        (Join-Path $InternalRuntimeRoot "app.pyd"),
        (Join-Path $InternalRuntimeRoot "q_agent_function_module\ohresult.pyd")
    )
    foreach ($NativeModule in $NativeFirstPartyModules) {
        if (-not (Test-Path -LiteralPath $NativeModule)) {
            throw "Protected native module was not included: $NativeModule"
        }
    }
    $AllowedCompatibilitySource = Join-Path $InternalRuntimeRoot $LegacyIfcScriptRelativePath
    $ExposedFirstPartySources = @(
        Get-ChildItem -LiteralPath $InternalRuntimeRoot -Recurse -File |
            Where-Object {
                $_.Extension -eq ".py" -and
                $_.FullName -match "\\(app|q_agent_function_module)\\" -and
                $_.FullName -ne $AllowedCompatibilitySource
            }
    )
    if ($ExposedFirstPartySources.Count -gt 0) {
        throw "Protected Runtime contains raw first-party Python source: $($ExposedFirstPartySources.FullName -join ', ')"
    }
}

function Invoke-PackagedCheck {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [int]$Attempts = 5
    )

    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
        & $RuntimeExe @Arguments | Out-Host
        if ($LASTEXITCODE -eq 0) { return $true }
        if ($Attempt -lt $Attempts) {
            Write-Warning "Packaged Runtime check attempt $Attempt failed; waiting for the released files to become available."
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

$PreviousDoctorKey = [Environment]::GetEnvironmentVariable(
    "BEESYNC_LLM_API_KEY", "Process"
)
try {
    # The build verifies that the frozen Runtime can read an injected key; it
    # must not require or package a developer's production credential.
    [Environment]::SetEnvironmentVariable(
        "BEESYNC_LLM_API_KEY", "build-doctor-only", "Process"
    )
    $DoctorPassed = Invoke-PackagedCheck -Arguments @(
        "--doctor", "--data-dir", $SmokeData, "--runtime-version", $Version
    )
}
finally {
    [Environment]::SetEnvironmentVariable(
        "BEESYNC_LLM_API_KEY", $PreviousDoctorKey, "Process"
    )
}
if (-not $DoctorPassed) { throw "Packaged Runtime doctor check failed." }

$DependencyCheckArguments = @("--dependency-diagnose")
if ($IncludeBrowser) {
    $DependencyCheckArguments += "--include-browser-dependencies"
}
$DependencyCheckPassed = Invoke-PackagedCheck -Arguments $DependencyCheckArguments
if (-not $DependencyCheckPassed) { throw "Packaged Runtime dependency import check failed." }

# Reproduce the import chain used by DWG room extraction in the frozen
# executable.  It catches native-module collection conflicts before a user
# starts Revit/AutoCAD work; it does not contact either desktop application.
$RoomExtractorPassed = Invoke-PackagedCheck -Arguments @("--room-extractor-diagnose")
if (-not $RoomExtractorPassed) { throw "Packaged Runtime DWG room-extractor check failed." }

Write-Host "Runtime build ready: $RuntimeExe"
