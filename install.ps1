# Install hubbleflow on Windows. Safe to re-run; upgrades in place.
#   irm https://raw.githubusercontent.com/<you>/hubbleflow/main/install.ps1 | iex
#   or, from a clone:  .\install.ps1
$ErrorActionPreference = 'Stop'

function Ok   ($m) { Write-Host "  " -NoNewline; Write-Host "OK " -ForegroundColor Green -NoNewline; Write-Host $m }
function Warn ($m) { Write-Host "  " -NoNewline; Write-Host "!  " -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Die  ($m) { Write-Host "  " -NoNewline; Write-Host "X  " -ForegroundColor Red -NoNewline; Write-Host $m; exit 1 }

$SourceDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$ConfigDir = if ($env:HUBBLEFLOW_HOME) { $env:HUBBLEFLOW_HOME } else { Join-Path $HOME '.hubbleflow' }

Write-Host ""
Write-Host "Installing hubbleflow" -ForegroundColor White
Write-Host ""

# ---- prerequisites -------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Warn "uv not found - installing it (https://docs.astral.sh/uv/)"
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$HOME\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Die "uv installed but not on PATH; open a new terminal and re-run"
    }
}
Ok "uv $((uv --version) -split ' ' | Select-Object -Last 1)"
Ok "python 3.13+ (uv will fetch it if needed)"

# PowerShell is what the agent's shell tool drives on Windows.
if (-not (Get-Command powershell -ErrorAction SilentlyContinue) -and
    -not (Get-Command pwsh -ErrorAction SilentlyContinue)) {
    Die "no PowerShell found - the agent's shell tool needs it"
}
Ok "powershell available (the agent runs commands through it)"

# ---- install -------------------------------------------------------------
Write-Host ""
uv tool install --from $SourceDir hubbleflow --force 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Die "install failed - try: uv tool install --from '$SourceDir' hubbleflow --force" }

$Bin = (Get-Command hubbleflow -ErrorAction SilentlyContinue)
if (-not $Bin) { Die "installed, but hubbleflow is not on PATH - open a new terminal and check" }
Ok "hubbleflow $((hubbleflow --version) -split ' ' | Select-Object -Last 1) -> $($Bin.Source)"

# ---- config --------------------------------------------------------------
Write-Host ""
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$EnvFile = Join-Path $ConfigDir '.env'
if (-not (Test-Path $EnvFile)) {
@'
# Fill in whichever providers you use. None are required to start -
# a local Ollama or Mesh model needs no key at all.
# GOOGLE_API_KEY=      # https://aistudio.google.com/apikey
# NVIDIA_API_KEY=      # https://build.nvidia.com
'@ | Set-Content -Path $EnvFile -Encoding utf8
    Ok "created $EnvFile (keys go here)"
} else {
    Ok "kept your existing $EnvFile"
}

Write-Host ""
Write-Host "Done. Run 'hubbleflow' in any project directory." -ForegroundColor White
Write-Host ""
Write-Host "  hubbleflow --help      every flag" -ForegroundColor DarkGray
Write-Host "  /help inside           every command" -ForegroundColor DarkGray
Write-Host "  .\uninstall.ps1        remove it again" -ForegroundColor DarkGray
Write-Host ""
