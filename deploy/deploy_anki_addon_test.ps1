# Copies src/Noteck -> Anki2/addons21/NoteckTest and patches the deployed copy
# so it can run beside the production add-on without sharing state.

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot  = Split-Path -Parent $ScriptDir
$Source       = Join-Path $ProjectRoot "src\Noteck"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

# Keep the test add-on in a distinct Anki add-ons folder from production Noteck.
$AddonName    = "NoteckTest"

# Anki add-ons folder (Windows default)
$AnkiAddons   = Join-Path $env:APPDATA "Anki2\addons21"
$Dest         = Join-Path $AnkiAddons $AddonName
$Vendor       = Join-Path $Dest "_vendor"
$VenvPython   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Replace-FileText {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$OldText,
    [Parameter(Mandatory = $true)]
    [string]$NewText
  )

  if (!(Test-Path $Path)) {
    throw "Patch target not found: $Path"
  }

  $Content = Get-Content -Raw -Path $Path
  if (!$Content.Contains($OldText)) {
    throw "Expected text not found in ${Path}: $OldText"
  }

  Set-Content -Path $Path -Value $Content.Replace($OldText, $NewText) -NoNewline
}

function Patch-TestAddon {
  param(
    [Parameter(Mandatory = $true)]
    [string]$AddonPath
  )

  $InitPath     = Join-Path $AddonPath "__init__.py"
  $UiPath       = Join-Path $AddonPath "ui\ui.py"
  $CardsPath    = Join-Path $AddonPath "modules\cards.py"
  $PagesPath    = Join-Path $AddonPath "modules\pages.py"
  $SyncPath     = Join-Path $AddonPath "modules\sync.py"
  $SettingsPath = Join-Path $AddonPath "modules\settings.py"

  # Keep profile database state completely separate from the production add-on.
  Replace-FileText $InitPath 'Path(profile_folder) / "Noteck" / "db" / "notion_integration.db"' 'Path(profile_folder) / "NoteckTest" / "db" / "notion_integration_test.db"'
  Replace-FileText $UiPath 'profile_folder / "Noteck" / "db" / "notion_integration.db"' 'profile_folder / "NoteckTest" / "db" / "notion_integration_test.db"'

  # Give the test add-on its own toolbar identity and visible window label.
  Replace-FileText $UiPath 'cmd="notion"' 'cmd="notion_test"'
  Replace-FileText $UiPath 'label="Notion"' 'label="Notion (test)"'
  Replace-FileText $UiPath 'tip="Open Notion add-on window"' 'tip="Open Notion test add-on window"'
  Replace-FileText $UiPath 'id="notion"' 'id="notion_test"'
  Replace-FileText $UiPath 'self.setWindowTitle("Notion")' 'self.setWindowTitle("Notion (test)")'

  # Store the test Notion API key in a separate OS keyring service.
  Replace-FileText $SettingsPath '_DEFAULT_SERVICE_NAME = "Noteck"' '_DEFAULT_SERVICE_NAME = "NoteckTest"'

  # Avoid modifying or reusing production note types and templates.
  Replace-FileText $CardsPath 'MODEL_NAME = "Notion (Basic)"' 'MODEL_NAME = "Notion (test) (Basic)"'
  Replace-FileText $CardsPath 'MODEL_NAME_BASIC_REVERSED = "Notion (Basic+Reversed)"' 'MODEL_NAME_BASIC_REVERSED = "Notion (test) (Basic+Reversed)"'
  Replace-FileText $CardsPath 'MODEL_NAME_INPUT = "Notion (Input)"' 'MODEL_NAME_INPUT = "Notion (test) (Input)"'
  Replace-FileText $CardsPath 'MODEL_NAME_CLOZE = "Notion (Cloze)"' 'MODEL_NAME_CLOZE = "Notion (test) (Cloze)"'
  Replace-FileText $CardsPath 'BASIC_CARD_NAME = "Notion (Basic)"' 'BASIC_CARD_NAME = "Notion (test) (Basic)"'
  Replace-FileText $CardsPath 'REVERSED_CARD_NAME = "Notion (Reversed)"' 'REVERSED_CARD_NAME = "Notion (test) (Reversed)"'
  Replace-FileText $CardsPath 'INPUT_CARD_NAME = "Notion (Input)"' 'INPUT_CARD_NAME = "Notion (test) (Input)"'
  Replace-FileText $CardsPath 'CLOZE_CARD_NAME = "Notion (Cloze)"' 'CLOZE_CARD_NAME = "Notion (test) (Cloze)"'
  Replace-FileText $CardsPath 'CSS_MANAGED_MARKER = "/* Noteck card model css */"' 'CSS_MANAGED_MARKER = "/* Noteck test card model css */"'

  # New page selections should create separate test decks by default.
  Replace-FileText $PagesPath 'def build_deck_names(roots: list[PageNode], prefix: str = "Notion") -> dict[str, str]:' 'def build_deck_names(roots: list[PageNode], prefix: str = "Notion (test)") -> dict[str, str]:'
  Replace-FileText $PagesPath 'prefix: str = "Notion",' 'prefix: str = "Notion (test)",'
  Replace-FileText $SyncPath 'normalized_stored_name = stored_deck_name.strip() or "Notion"' 'normalized_stored_name = stored_deck_name.strip() or "Notion (test)"'
}

if (!(Test-Path $Source)) {
  throw "Source folder not found: $Source"
}

# Ensure destination exists (robocopy wants it)
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

Write-Host "Deploying test add-on from $Source to $Dest"

# /MIR mirrors directory tree (including deletions)
# /NFL /NDL reduce noise, /NJH /NJS removes header/summary, /R:1 /W:1 fast retry
robocopy $Source $Dest /MIR /XD "__pycache__" "_vendor" /R:1 /W:1 /NFL /NDL /NJH /NJS

# Robocopy returns "weird" exit codes; >= 8 indicates a failure
if ($LASTEXITCODE -ge 8) {
  throw "Robocopy failed with exit code $LASTEXITCODE"
}

Patch-TestAddon -AddonPath $Dest

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

Write-Host "Done. Test add-on deployed as $AddonName."
