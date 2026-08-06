# Build the OpenOptima Windows application.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Requires Python 3.11+ on PATH. Everything else is fetched into a throwaway
# virtual environment so the build cannot be polluted by whatever happens to be
# installed on the machine.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== Creating a clean build environment =="
if (Test-Path .venv-build) { Remove-Item -Recurse -Force .venv-build }
python -m venv .venv-build
$py = ".\.venv-build\Scripts\python.exe"

& $py -m pip install --upgrade pip wheel --quiet
& $py -m pip install -e ".[optimise]" --quiet
& $py -m pip install pyinstaller --quiet

Write-Host "== Checking it runs before freezing it =="
& $py -m pytest tests\unit -q
if ($LASTEXITCODE -ne 0) { throw "unit tests failed; not building" }

Write-Host "== Freezing =="
if (Test-Path dist) { Remove-Item -Recurse -Force dist }
& $py -m PyInstaller packaging\openoptima.spec --noconfirm --clean

Write-Host "== Smoke-testing the built application =="
$exe = "dist\OpenOptima\OpenOptima.exe"
if (-not (Test-Path $exe)) { throw "build produced no executable" }

# Start it, confirm it answers, then stop it. A build that starts and then dies
# on the first real request is worse than one that fails to build.
$proc = Start-Process -FilePath $exe -ArgumentList "--no-browser","--port","8791" -PassThru
Start-Sleep -Seconds 12
try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8791/api/status" -TimeoutSec 30
    Write-Host ("   solver found: {0}" -f $status.solver_available)
    Write-Host ("   gmsh: {0}" -f $status.versions.gmsh)
    if (-not $status.versions.gmsh -or $status.versions.gmsh -eq "not installed") {
        throw "the frozen build cannot load gmsh - check collect_dynamic_libs in the spec"
    }
} finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Built: $root\dist\OpenOptima\OpenOptima.exe"
Write-Host "Zip that folder, or run packaging\installer.iss with Inno Setup to make an installer."
