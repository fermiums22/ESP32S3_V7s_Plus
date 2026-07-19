[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$stm32Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\V7s_Plus")).Path
$cubeIde = if ($env:STM32CUBEIDE) {
    $env:STM32CUBEIDE
}
else {
    "C:\ST\STM32CubeIDE_2.0.0\STM32CubeIDE\stm32cubeidec.exe"
}
if (-not (Test-Path -LiteralPath $cubeIde)) {
    throw "stm32cubeidec.exe was not found: $cubeIde. Set STM32CUBEIDE to its full path."
}

$workspace = "D:\w_space\.stm32cubeide-headless-workspace"
Write-Host "Building STM32 Debug only; ESP32-S3 is not involved..."
& $cubeIde -nosplash -application org.eclipse.cdt.managedbuilder.core.headlessbuild `
    -data $workspace -import $stm32Root -build "V7s_Plus/Debug"
if ($LASTEXITCODE -ne 0) {
    throw "STM32 Debug build failed with exit code $LASTEXITCODE."
}

$elf = Join-Path $stm32Root "Debug\V7s_Plus.elf"
$binary = Join-Path $stm32Root "Debug\V7s_Plus.bin"
if (-not (Test-Path -LiteralPath $elf)) {
    throw "STM32 build completed but did not produce $elf."
}

# CubeIDE's managed build does not add a .bin post-build step.  Always derive
# the upload image from the just-linked ELF so the S3 never receives a stale
# firmware file from a previous build.
$objcopy = Get-ChildItem (Join-Path (Split-Path -Parent $cubeIde) "plugins") `
    -Recurse -Filter "arm-none-eabi-objcopy.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $objcopy) {
    throw "arm-none-eabi-objcopy.exe was not found in the STM32CubeIDE installation."
}
& $objcopy -O binary $elf $binary
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $binary)) {
    throw "Failed to create the STM32 binary image from $elf."
}
Write-Host "STM32 image ready: $binary"
