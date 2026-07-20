param(
    [string]$HaAddonsPath = "\\homeassistant\addons",
    [string]$HaConfigPath = "\\homeassistant\config"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "ha_addons\gopro_assist_bridge"
if (-not (Test-Path -LiteralPath (Join-Path $source "config.yaml"))) {
    throw "Add-on source not found: $source"
}
if (-not (Test-Path -LiteralPath $HaAddonsPath)) {
    throw "Home Assistant addons share not found: $HaAddonsPath"
}

$target = Join-Path $HaAddonsPath "gopro_assist_bridge"
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item -Path (Join-Path $source "*") -Destination $target -Recurse -Force

Write-Host "Sokol-9 add-on copied to $target"
if (Test-Path -LiteralPath $HaConfigPath) {
    $packages = Join-Path $HaConfigPath "packages"
    New-Item -ItemType Directory -Path $packages -Force | Out-Null
    $packageSource = Join-Path $repoRoot "Docs\home-assistant\robot_ai.yaml"
    $packageTarget = Join-Path $packages "sokol9.yaml"
    Copy-Item -LiteralPath $packageSource -Destination $packageTarget -Force
    Write-Host "Sokol-9 Home Assistant package copied to $packageTarget"
    $configuration = Join-Path $HaConfigPath "configuration.yaml"
    if ((Test-Path -LiteralPath $configuration) -and
        -not (Select-String -LiteralPath $configuration -Pattern "!include_dir_named\s+packages" -Quiet)) {
        Write-Warning "configuration.yaml does not mention !include_dir_named packages; enable HA packages before restart."
    }
} else {
    Write-Warning "Home Assistant config share not found: $HaConfigPath. Copy Docs/home-assistant/robot_ai.yaml manually."
}
Write-Host "In Home Assistant: Settings -> Apps -> App store -> Check for updates, then install/rebuild GoPro Assist Bridge."
