[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$TargetHost,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Token,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Username,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Password,

    [string]$Firmware = (Join-Path $PSScriptRoot "..\..\V7s_Plus\Debug\V7s_Plus.bin"),

    [ValidateRange(1, 65535)]
    [int]$Port = 80
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$firmwarePath = (Resolve-Path -LiteralPath $Firmware).Path
$firmwareInfo = Get-Item -LiteralPath $firmwarePath
if ($firmwareInfo.Length -lt 8 -or $firmwareInfo.Length -gt 65536) {
    throw "STM32 firmware must be between 8 and 65536 bytes; got $($firmwareInfo.Length)."
}

$header = [System.IO.File]::ReadAllBytes($firmwarePath)[0..7]
$stackPointer = [BitConverter]::ToUInt32($header, 0)
$resetHandler = [BitConverter]::ToUInt32($header, 4)
$resetAddress = $resetHandler -band 0xFFFFFFFE
if ($stackPointer -lt 0x20000000 -or $stackPointer -ge 0x20020000 -or
    (($stackPointer -band 3) -ne 0) -or (($resetHandler -band 1) -ne 1) -or
    $resetAddress -lt 0x08000000 -or $resetAddress -ge 0x08010000) {
    throw "Firmware does not have a valid STM32F0 vector table. Refusing upload."
}

Add-Type -AssemblyName System.Net.Http
$client = [System.Net.Http.HttpClient]::new()
$multipart = [System.Net.Http.MultipartFormDataContent]::new()
$stream = [System.IO.File]::OpenRead($firmwarePath)
$fileContent = [System.Net.Http.StreamContent]::new($stream)
$fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("application/octet-stream")
$multipart.Add($fileContent, "firmware", [System.IO.Path]::GetFileName($firmwarePath))

try {
    $uri = "http://${TargetHost}:$Port/api/v1/stm32-firmware"
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, $uri)
    $credentialBytes = [Text.Encoding]::ASCII.GetBytes("${Username}:$Password")
    $request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new(
        "Basic", [Convert]::ToBase64String($credentialBytes))
    $request.Headers.Add("X-V7S-Token", $Token)
    $request.Content = $multipart

    Write-Host "Uploading $($firmwareInfo.Name) ($($firmwareInfo.Length) B) to $uri ..."
    $response = $client.SendAsync($request).GetAwaiter().GetResult()
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) {
        throw "S3 rejected the image: HTTP $([int]$response.StatusCode) $($response.ReasonPhrase): $body"
    }
    Write-Host $body
    Write-Host "Upload is complete. In Home Assistant press 'Flash staged STM32 firmware' before restarting the S3."
}
finally {
    $multipart.Dispose()
    $client.Dispose()
}
