param(
    [Parameter(Mandatory = $true)]
    [string]$SourceDir,

    [Parameter(Mandatory = $true)]
    [string]$ManifestFile
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    Write-Error "Source directory not found: $SourceDir"
    exit 2
}

if (-not (Test-Path -LiteralPath $ManifestFile -PathType Leaf)) {
    Write-Error "Manifest file not found: $ManifestFile"
    exit 2
}

try {
    $manifest = Get-Content -LiteralPath $ManifestFile -Raw | ConvertFrom-Json
} catch {
    Write-Error "Manifest JSON is invalid: $ManifestFile"
    exit 4
}

if (-not $manifest.files) {
    Write-Error "Manifest does not contain a files list."
    exit 4
}

$hasErrors = $false

foreach ($entry in $manifest.files) {
    if (-not $entry.path -or -not $entry.sha256) {
        Write-Error "Manifest entry is missing path or sha256."
        $hasErrors = $true
        continue
    }

    $targetPath = Join-Path $SourceDir ($entry.path -replace '/', '\\')
    if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
        Write-Error "Missing file in payload: $($entry.path)"
        $hasErrors = $true
        continue
    }

    $actualHash = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $expectedHash = ([string]$entry.sha256).ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        Write-Error "Checksum mismatch: $($entry.path)"
        $hasErrors = $true
    }
}

if ($hasErrors) {
    exit 3
}

if ($manifest.version) {
    Write-Host "Manifest validation passed for version $($manifest.version)."
} else {
    Write-Host "Manifest validation passed."
}
exit 0
