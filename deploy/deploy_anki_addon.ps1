# Copies src/add-on -> Anki2/addons21/<addon-folder-name>

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot  = Split-Path -Parent $ScriptDir
$Source      = Join-Path $ProjectRoot "src\Noteck"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

# Change this to your add-on folder name (what you want it to be called in Anki)
$AddonName   = "Noteck"

# Anki add-ons folder (Windows default)
$AnkiAddons  = Join-Path $env:APPDATA "Anki2\addons21"
$Dest        = Join-Path $AnkiAddons $AddonName
$Vendor      = Join-Path $Dest "_vendor"
$VenvPython  = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (!(Test-Path $Source)) {
  throw "Source folder not found: $Source"
}

# Ensure destination exists (robocopy wants it)
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

Write-Host "Deploying from $Source to $Dest"

# /MIR mirrors directory tree (including deletions)
# /NFL /NDL reduce noise, /NJH /NJS removes header/summary, /R:1 /W:1 fast retry
robocopy $Source $Dest /MIR /XD "__pycache__" "_vendor" /R:1 /W:1 /NFL /NDL /NJH /NJS

# Robocopy returns "weird" exit codes; >= 8 indicates a failure
if ($LASTEXITCODE -ge 8) {
  throw "Robocopy failed with exit code $LASTEXITCODE"
}

if (Test-Path $Requirements) {
  if (Test-Path $Vendor) {
    Remove-Item -Recurse -Force -Path $Vendor
  }
  New-Item -ItemType Directory -Force -Path $Vendor | Out-Null

  # Install dependencies into the add-on folder so Anki never has to run pip at startup.
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

  Write-Host "Installing Python dependencies into $Vendor"
  & $PythonExe @PythonArgs -m pip install --upgrade --target $Vendor -r $Requirements
  if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed with exit code $LASTEXITCODE"
  }
}

Write-Host "Done."
