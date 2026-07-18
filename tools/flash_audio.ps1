param(
    [string]$Port = "COM23"
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$sourceDir = Join-Path $root "filesystem"
$sourceTrack = Join-Path $sourceDir "Balensiaga.mp3"
$buildDir = Join-Path $root ".esphome\build\v7s-plus"
$image = Join-Path $buildDir "audio.spiffs.bin"
$backupDir = Join-Path $root ".esphome\backups"

$audioOffset = 0xA00000
$audioSize = 0x600000

if (-not (Test-Path -LiteralPath $python)) {
    throw "ESPHome virtual environment is missing. Run setup_esphome.bat first."
}
if (-not (Test-Path -LiteralPath $sourceTrack)) {
    throw "Default track is missing: $sourceTrack"
}
if ((Get-Item -LiteralPath $sourceTrack).Length -ge $audioSize) {
    throw "Balensiaga.mp3 does not fit in the 6 MB audio partition."
}

$spiffsGen = Get-ChildItem -Path (Join-Path $root ".esphome\idf\frameworks") -Directory |
    Sort-Object Name -Descending |
    ForEach-Object { Join-Path $_.FullName "components\spiffs\spiffsgen.py" } |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $spiffsGen) {
    throw "ESP-IDF spiffsgen.py was not found. Compile the S3 firmware first."
}

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

& $python $spiffsGen $audioSize $sourceDir $image
if ($LASTEXITCODE -ne 0) {
    throw "SPIFFS image generation failed."
}
if ((Get-Item -LiteralPath $image).Length -ne $audioSize) {
    throw "Unexpected SPIFFS image size."
}

$chipInfo = (& $python -m esptool --port $Port chip-id 2>&1 | Out-String)
Write-Host $chipInfo
if ($LASTEXITCODE -ne 0 -or $chipInfo -notmatch "ESP32-S3") {
    throw "$Port is not the expected ESP32-S3. Audio partition was not touched."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $backupDir "audio-before-$timestamp.bin"

Write-Host "Backing up the current audio partition to $backup"
& $python -m esptool --chip esp32s3 --port $Port --baud 460800 read-flash $audioOffset $audioSize $backup
if ($LASTEXITCODE -ne 0) {
    throw "Audio partition backup failed. Nothing was written."
}

Write-Host "Writing Balensiaga.mp3 SPIFFS image at 0xA00000"
& $python -m esptool --chip esp32s3 --port $Port --baud 460800 write-flash $audioOffset $image
if ($LASTEXITCODE -ne 0) {
    throw "Audio partition write failed. Backup: $backup"
}

Write-Host "Audio filesystem flashed and verified. Backup: $backup"
