$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"
$python = $null

foreach ($version in @("3.12", "3.13", "3.11", "3.14")) {
    & py "-$version" -c "import sys; assert sys.maxsize > 2**32" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = @("py", "-$version")
        break
    }
}

if ($null -eq $python) {
    throw "Python x64 3.11-3.14 not found. Install it from python.org."
}

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & $python[0] $python[1] -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python environment." }
}

$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip." }

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Failed to install ESPHome Core." }

& $venvPython -m esphome version
