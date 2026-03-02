param(
    [Parameter(Mandatory = $true)]
    [string]$SourceDir,

    [Parameter(Mandatory = $true)]
    [string]$OutputFile,

    [Parameter(Mandatory = $false)]
    [string]$VersionFile
)

$ErrorActionPreference = 'Stop'

$resolvedSource = (Resolve-Path -Path $SourceDir).Path

if (-not (Test-Path -LiteralPath $resolvedSource -PathType Container)) {
    throw "Source directory not found: $SourceDir"
}

$version = $null
if ($VersionFile -and (Test-Path -LiteralPath $VersionFile -PathType Leaf)) {
    $version = (Get-Content -LiteralPath $VersionFile -Raw).Trim()
}

if ([string]::IsNullOrWhiteSpace($version)) {
    $version = Get-Date -Format 'yyyy.MM.dd.HHmm'
}

$sourceWithSlash = $resolvedSource
if (-not $sourceWithSlash.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
    $sourceWithSlash = $sourceWithSlash + [System.IO.Path]::DirectorySeparatorChar
}

$sourceUri = [System.Uri]$sourceWithSlash

$files = Get-ChildItem -LiteralPath $resolvedSource -Recurse -File |
    Sort-Object FullName |
    ForEach-Object {
        $fileUri = [System.Uri]$_.FullName
        $relativePath = $sourceUri.MakeRelativeUri($fileUri).ToString().Replace('%20', ' ')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        [PSCustomObject]@{
            path   = $relativePath
            sha256 = $hash
            size   = $_.Length
        }
    }

$manifest = [PSCustomObject]@{
    version             = $version
    generatedAtUtc      = (Get-Date).ToUniversalTime().ToString('o')
    minSupportedVersion = ''
    notes               = ''
    files               = $files
}

$outputDir = Split-Path -Parent $OutputFile
if ($outputDir -and -not (Test-Path -LiteralPath $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputFile -Encoding UTF8
Write-Host "Manifest generated: $OutputFile"
