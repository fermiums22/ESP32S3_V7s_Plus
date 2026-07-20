param(
    [string]$HaUrl = "http://homeassistant.local:8123",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$token = $env:SOKOL9_HA_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "Set SOKOL9_HA_TOKEN to a Home Assistant long-lived access token for this PowerShell session."
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot "sokol9_home_probe.json"
}

$baseUrl = $HaUrl.TrimEnd("/")
$headers = @{ Authorization = "Bearer $token" }
$config = Invoke-RestMethod -Uri "$baseUrl/api/config" -Headers $headers -Method Get
$states = @(Invoke-RestMethod -Uri "$baseUrl/api/states" -Headers $headers -Method Get)
$services = @(Invoke-RestMethod -Uri "$baseUrl/api/services" -Headers $headers -Method Get)

function Select-EntitySummary {
    param([object[]]$Items)
    @($Items | ForEach-Object {
        [ordered]@{
            entity_id = $_.entity_id
            state = $_.state
            name = $_.attributes.friendly_name
            device_class = $_.attributes.device_class
            model = $_.attributes.model
            supported_features = $_.attributes.supported_features
            entity_picture = $_.attributes.entity_picture
        }
    })
}

function Domain-Is {
    param([object]$Item, [string[]]$Domains)
    foreach ($domain in $Domains) {
        if ($Item.entity_id -like "$domain.*") { return $true }
    }
    return $false
}

$voice = $states | Where-Object { Domain-Is $_ @("stt", "tts", "conversation") }
$vacuum = $states | Where-Object { Domain-Is $_ @("vacuum") }
$mika = $states | Where-Object {
    "$($_.entity_id) $($_.attributes.friendly_name) $($_.attributes.model)" -match "x20|xiaomi|robot vacuum|mi robot|мика"
}
$cameras = $states | Where-Object { Domain-Is $_ @("camera", "image") }
$maps = $cameras | Where-Object {
    $text = "$($_.entity_id) $($_.attributes.friendly_name) $($_.attributes.model)".ToLowerInvariant()
    $text -match "x20|xiaomi|vacuum|map|карта|пылесос"
}
$presence = $states | Where-Object {
    (Domain-Is $_ @("person", "device_tracker")) -or
    ($_.entity_id -like "binary_sensor.*" -and
     $_.attributes.device_class -in @("motion", "occupancy", "presence"))
}
$media = $states | Where-Object { Domain-Is $_ @("media_player", "remote") }
$robot = $states | Where-Object {
    "$($_.entity_id) $($_.attributes.friendly_name)" -match "v7s|gopro|sokol|сокол|jbl"
}

$serviceDomains = @("vacuum", "xiaomi_miio", "media_player", "remote", "tts", "script", "scene")
$selectedServices = @($services | Where-Object { $_.domain -in $serviceDomains } | ForEach-Object {
    [ordered]@{
        domain = $_.domain
        services = @($_.services.PSObject.Properties.Name)
    }
})

$result = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    home_assistant = [ordered]@{
        url = $baseUrl
        version = $config.version
        location_name = $config.location_name
        time_zone = $config.time_zone
    }
    voice = Select-EntitySummary $voice
    robot = Select-EntitySummary $robot
    vacuum = Select-EntitySummary $vacuum
    mika_candidates = Select-EntitySummary $mika
    map_candidates = Select-EntitySummary $maps
    cameras_and_images = Select-EntitySummary $cameras
    presence = Select-EntitySummary $presence
    media_and_tv = Select-EntitySummary $media
    relevant_services = $selectedServices
}

$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Host "Home Assistant $($config.version) probe saved to $OutputPath"
Write-Host "Found: voice=$($voice.Count), vacuum=$($vacuum.Count), Mika=$($mika.Count), maps=$($maps.Count), presence=$($presence.Count), media=$($media.Count)"
