param(
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "ha_addons\gopro_assist_bridge"
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $dist = Join-Path $repoRoot "dist"
    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    $Destination = Join-Path $dist "sokol9-ha-addon.zip"
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$destinationFull = [IO.Path]::GetFullPath($Destination)
if ([IO.File]::Exists($destinationFull)) {
    [IO.File]::Delete($destinationFull)
}
$archive = [IO.Compression.ZipFile]::Open($destinationFull, [IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem -LiteralPath $source -File -Recurse | Where-Object {
        $_.Extension -ne ".pyc" -and $_.FullName -notmatch "[\\/]__pycache__[\\/]"
    } | ForEach-Object {
        $relative = $_.FullName.Substring($source.Length).TrimStart([char[]]"\/")
        $entry = "gopro_assist_bridge/" + $relative.Replace("\", "/")
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $_.FullName, $entry, [IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
} finally {
    $archive.Dispose()
}
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Destination
Write-Host "Package: $Destination"
Write-Host "SHA256: $($hash.Hash)"
