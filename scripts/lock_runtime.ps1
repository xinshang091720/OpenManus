[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $PythonVersion -ne "3.12") {
    throw "Python 3.12 is required to create the Windows Runtime dependency lock."
}

& $Python -m pip install "uv==0.5.30"
& $Python -m uv pip compile (Join-Path $Root "requirements.txt") `
    --python $Python `
    --output-file (Join-Path $Root "requirements-runtime.lock")
if ($LASTEXITCODE -ne 0) { throw "Dependency lock generation failed." }

Write-Host "Generated requirements-runtime.lock for Windows/Python 3.12."
