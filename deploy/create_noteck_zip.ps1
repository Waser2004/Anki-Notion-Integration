<#
  Compresses `src/Noteck` into a versioned ZIP inside `deploy`.

  Usage: `pwsh deploy/create_noteck_zip.ps1 -DeployVersion 1.2.3`
#>

param (
    [Parameter(Mandatory = $true)]
    [string]$DeployVersion
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$Source = Join-Path $ProjectRoot "src\Noteck"
$DeployDir = Join-Path $ProjectRoot "deploy"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$ReleaseNotes = Join-Path $ProjectRoot "CHANGELOG.md"
$ReleaseNoteAssets = Join-Path $ProjectRoot "docs\release-notes-assets"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Source)) {
    throw "Source folder not found: $Source"
}

if (-not (Test-Path $ReleaseNotes)) {
    throw "Release notes not found: $ReleaseNotes"
}

if (-not (Test-Path $DeployDir)) {
    New-Item -ItemType Directory -Force -Path $DeployDir | Out-Null
}

$ArchiveName = "Noteck-$DeployVersion.zip"
$ArchivePath = Join-Path $DeployDir $ArchiveName

if (Test-Path $ArchivePath) {
    Remove-Item -Force -Path $ArchivePath
}

$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("NoteckZip_" + [System.Guid]::NewGuid().ToString("N"))
$StagingSource = Join-Path $TempRoot "staging"
$StagingPrefix = $StagingSource.TrimEnd("\", "/") + "\"

try {
    New-Item -ItemType Directory -Force -Path $StagingSource | Out-Null

    # Stage source files while excluding Python cache artifacts.
    robocopy $Source $StagingSource /MIR /XD "__pycache__" "_vendor" /XF "*.pyc" "*.pyo" /R:1 /W:1 /NFL /NDL /NJH /NJS
    if ($LASTEXITCODE -ge 8) {
        throw "Robocopy failed with exit code $LASTEXITCODE"
    }

    # Bundle the canonical changelog and optional local images beside the add-on.
    Copy-Item -LiteralPath $ReleaseNotes -Destination (Join-Path $StagingSource "CHANGELOG.md")
    if (Test-Path $ReleaseNoteAssets) {
        $StagingAssets = Join-Path $StagingSource "docs\release-notes-assets"
        New-Item -ItemType Directory -Force -Path $StagingAssets | Out-Null
        Copy-Item -Path (Join-Path $ReleaseNoteAssets "*") -Destination $StagingAssets -Recurse -Force
    }

    if (Test-Path $Requirements) {
        $Vendor = Join-Path $StagingSource "_vendor"
        New-Item -ItemType Directory -Force -Path $Vendor | Out-Null

        # Package dependencies with the add-on so installation does not depend on Anki running pip.
        if (Test-Path $VenvPython) {
            $PythonExe = $VenvPython
            $PythonArgs = @()
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $PythonExe = "py"
            $PythonArgs = @("-3")
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $PythonExe = "python"
            $PythonArgs = @()
        } else {
            throw "No Python interpreter found for installing add-on dependencies."
        }

        Write-Host "Installing Python dependencies into staged add-on"
        & $PythonExe @PythonArgs -m pip install --upgrade --target $Vendor -r $Requirements
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed with exit code $LASTEXITCODE"
        }
    }

    $ArchiveFiles = Get-ChildItem -LiteralPath $StagingSource -Recurse -File -Force
    if ($ArchiveFiles.Count -eq 0) {
        throw "No files found to archive in staging folder: $StagingSource"
    }

    Write-Host "Creating archive $ArchivePath from staged files"

    # Build the zip manually so entry names always use forward slashes.
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $Archive = [System.IO.Compression.ZipFile]::Open(
        $ArchivePath,
        [System.IO.Compression.ZipArchiveMode]::Create
    )

    try {
        foreach ($ArchiveFile in $ArchiveFiles) {
            # Avoid GetRelativePath() so the script also works in Windows PowerShell 5.1.
            $RelativePath = $ArchiveFile.FullName.Substring($StagingPrefix.Length)
            $EntryName = $RelativePath -replace "\\", "/"

            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $Archive,
                $ArchiveFile.FullName,
                $EntryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
    finally {
        $Archive.Dispose()
    }

    Write-Host "Archive ready: $ArchivePath"
}
finally {
    if (Test-Path $TempRoot) {
        Remove-Item -Recurse -Force -Path $TempRoot
    }
}
