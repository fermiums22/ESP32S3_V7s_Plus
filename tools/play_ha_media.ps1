param(
  [Parameter(Position = 0)]
  [string]$Track = "Balensiaga.mp3",

  [string]$Profile = "$env:USERPROFILE\Desktop\hasistant.tlp"
)

$ErrorActionPreference = "Stop"

if ([IO.Path]::GetFileName($Track) -ne $Track -or [IO.Path]::GetExtension($Track) -ne ".mp3") {
  throw "Track must be a plain .mp3 filename from /media/v7s_music."
}

$Sexec = "C:\Program Files (x86)\Bitvise SSH Client\sexec.exe"
if (-not (Test-Path -LiteralPath $Sexec)) {
  throw "Bitvise sexec.exe was not found: $Sexec"
}
if (-not (Test-Path -LiteralPath $Profile)) {
  throw "Bitvise SSH profile was not found: $Profile"
}

$EncodedTrack = [Uri]::EscapeDataString($Track).Replace("%2F", "/")
$Payload = @{
  entity_id = "media_player.v7s_plus_wifi_audio"
  media_content_id = "media-source://media_source/local/v7s_music/$EncodedTrack"
  media_content_type = "audio/mp3"
  announce = $true
} | ConvertTo-Json -Compress
$PayloadBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Payload))

$RemoteScript = "printf '%s' '$PayloadBase64' | base64 -d | curl --fail --silent --show-error -X POST -H `"Authorization: Bearer `$SUPERVISOR_TOKEN`" -H 'Content-Type: application/json' --data-binary @- http://supervisor/core/api/services/media_player/play_media"
$RemoteScriptBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($RemoteScript))
$RemoteCommand = "echo $RemoteScriptBase64|base64 -d|sh"

Write-Host "Playing '$Track' through HA -> S3 -> I2S -> ESP32 -> Bluetooth -> JBL..."
& $Sexec "-profile=$Profile" "-unat=y" "-exitZero" "-cmd=$RemoteCommand"
if ($LASTEXITCODE -ne 0) {
  throw "Home Assistant service call failed with exit code $LASTEXITCODE."
}
