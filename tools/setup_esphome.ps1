$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$requirements = Join-Path $root "requirements.txt"

if (-not (Test-Path $venvPython)) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    $python = $null

    if ($null -ne $launcher) {
        foreach ($version in @("3.12", "3.13", "3.11", "3.14")) {
            $savedErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            & $launcher.Source "-$version" -c "import sys; assert sys.maxsize > 2**32" *> $null
            $probeExitCode = $LASTEXITCODE
            $ErrorActionPreference = $savedErrorActionPreference

            if ($probeExitCode -eq 0) {
                $python = @($launcher.Source, "-$version")
                break
            }
        }
    }

    if ($null -eq $python) {
        throw "Python x64 3.11-3.14 not found. Install it from python.org."
    }

    & $python[0] $python[1] -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python environment." }
}

$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& $venvPython -m pip install --disable-pip-version-check --no-index -r $requirements *> $null
$requirementsReady = $LASTEXITCODE -eq 0
$ErrorActionPreference = $savedErrorActionPreference

if ($requirementsReady) {
    Write-Host "ESPHome dependencies are already installed; skipping download."
} else {
    & $venvPython -m pip install --disable-pip-version-check -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Failed to install ESPHome Core." }
}

& $venvPython -m esphome version
if ($LASTEXITCODE -ne 0) { throw "Failed to start ESPHome Core." }
