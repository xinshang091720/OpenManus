$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$projRoot = Resolve-Path "$scriptDir\.."
$publishDir = "$projRoot\bin\Release\publish"
$objDir = "$projRoot\obj"
$releaseDir = "$projRoot\bin\Release"
$isccPaths = @(
    "C:\Program Files\Inno Setup 7\ISCC.exe",
    "D:\Inno Setup 7\ISCC.exe",
    "E:\Inno Setup 7\ISCC.exe"
)

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " AiConstruction Packaging Script" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ---- Step 1: Clean ----
Write-Host "[1/4] Cleaning old build output..." -ForegroundColor Yellow

@($publishDir, $releaseDir, $objDir) | ForEach-Object {
    if (Test-Path $_) {
        Write-Host "  Deleting: $_"
        Remove-Item $_ -Recurse -Force -ErrorAction Continue
    }
}
Write-Host "[1/4] Clean done!" -ForegroundColor Green

# ---- Step 2: Publish ----
Write-Host ""
Write-Host "[2/4] Publishing project (latest source)..." -ForegroundColor Yellow
$publishArgs = @(
    "publish", "$projRoot\AiConstruction.csproj",
    "-c", "Release",
    "-r", "win-x64",
    "--self-contained", "false",
    "-o", $publishDir
)
$publishResult = & dotnet $publishArgs 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Publish failed!" -ForegroundColor Red
    Write-Host $publishResult
    pause
    exit 1
}
Write-Host "[2/4] Publish done!" -ForegroundColor Green

# ---- Step 3: Copy resources ----
Write-Host ""
Write-Host "[3/4] Copying extra resources..." -ForegroundColor Yellow

# BeeSync.AgentRuntime
$runtimeSrc = "$projRoot\bin\Debug\net8.0-windows\BeeSync.AgentRuntime"
if (Test-Path $runtimeSrc) {
    & taskkill /F /IM BeeSync.AgentRuntime.exe 2>$null
    Start-Sleep -Seconds 1
    $runtimeDest = "$publishDir\BeeSync.AgentRuntime"
    if (Test-Path $runtimeDest) { Remove-Item $runtimeDest -Recurse -Force }
    Copy-Item $runtimeSrc $runtimeDest -Recurse
    Write-Host "  BeeSync.AgentRuntime copied." -ForegroundColor Green
} else {
    Write-Host "  WARNING: BeeSync.AgentRuntime not found" -ForegroundColor Magenta
}

# logo.ico
$icoSrc = "$projRoot\logo.ico"
if (Test-Path $icoSrc) {
    Copy-Item $icoSrc "$publishDir\logo.ico" -Force
    Write-Host "  logo.ico copied." -ForegroundColor Green
} else {
    Write-Host "  WARNING: logo.ico not found" -ForegroundColor Magenta
}
Write-Host "[3/4] Resources done!" -ForegroundColor Green

# ---- Verify publish contents ----
Write-Host ""
Write-Host "  === Verify publish directory ===" -ForegroundColor Gray
$checkItems = @("AiConstruction.exe", "logo.ico", "AiConstruction.dll")
foreach ($item in $checkItems) {
    $fullPath = "$publishDir\$item"
    if (Test-Path $fullPath) {
        $time = (Get-Item $fullPath).LastWriteTime.ToString("HH:mm:ss")
        Write-Host "  [OK] $item  ($time)" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] $item" -ForegroundColor Red
    }
}

# ---- Step 4: Compile installer ----
Write-Host ""
Write-Host "[4/4] Compiling installer..." -ForegroundColor Yellow

$isccExe = $null
foreach ($path in $isccPaths) {
    if (Test-Path $path) { $isccExe = $path; break }
}

if (-not $isccExe) {
    Write-Host "Inno Setup not found!" -ForegroundColor Red
    pause
    exit 1
}

Write-Host "  Using: $isccExe"
$issFile = "$scriptDir\AiConstruction_Setup.iss"
& $isccExe $issFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "Compile failed!" -ForegroundColor Red
    pause
    exit 1
}

# ---- Done ----
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Package complete!" -ForegroundColor Cyan
$outputDir = Resolve-Path "$projRoot\..\Installer" -ErrorAction SilentlyContinue
if (-not $outputDir) { $outputDir = "$projRoot\..\Installer" }
Get-ChildItem "$outputDir\AiConstruction_Setup_*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | ForEach-Object {
    Write-Host "  $($_.FullName)" -ForegroundColor Cyan
    Write-Host "  Size: $([math]::Round($_.Length / 1MB, 1)) MB  Time: $($_.LastWriteTime)" -ForegroundColor Gray
}
Write-Host "============================================" -ForegroundColor Cyan
pause
