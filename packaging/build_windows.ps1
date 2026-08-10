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

# Everything the app imports lazily. Asking the server for its status is not
# enough on its own: the optimiser, the mesher, the solver adapter and the
# geometry provider are all imported the first time somebody actually uses
# them, so a build missing one of those starts perfectly and then dies in the
# middle of a study.
#
# This is not hypothetical. A build shipped with the optimiser unable to import
# at all -- pymoo needs moocore, moocore asks importlib.metadata for its own
# version, and PyInstaller does not bundle .dist-info folders unless told to.
# The status check walked straight past it. See copy_metadata in the spec.
#
# Run with Start-Process -Wait, not `&`. The executable is built for the
# Windows GUI subsystem, so a shell does not wait for it and never sees its
# exit code. For the same reason it cannot print to this console: it has no
# stdout at all, and writes to its log instead. That is what gets read back.
Write-Host "-- runtime imports"
$checkHome = Join-Path $env:TEMP "openoptima-build-check"
Remove-Item -Recurse -Force $checkHome -ErrorAction SilentlyContinue
$env:OPENOPTIMA_CONFIG_DIR = $checkHome
$check = Start-Process -FilePath $exe -ArgumentList "--self-check" -PassThru -Wait
Get-Content (Join-Path $checkHome "openoptima.log") -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host ("   " + $_) }
Remove-Item Env:\OPENOPTIMA_CONFIG_DIR -ErrorAction SilentlyContinue
if ($check.ExitCode -ne 0) {
    throw "the frozen build cannot import everything it needs at runtime; see above"
}

# Then start it and confirm it actually answers.
Write-Host "-- serving"
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
