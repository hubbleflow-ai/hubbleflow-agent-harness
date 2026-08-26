# Remove hubbleflow on Windows. Config and sessions are kept unless -Purge.
param([switch]$Purge)
$ErrorActionPreference = 'Stop'

function Ok   ($m) { Write-Host "  " -NoNewline; Write-Host "OK " -ForegroundColor Green -NoNewline; Write-Host $m }
function Warn ($m) { Write-Host "  " -NoNewline; Write-Host "!  " -ForegroundColor Yellow -NoNewline; Write-Host $m }

$ConfigDir = if ($env:HUBBLEFLOW_HOME) { $env:HUBBLEFLOW_HOME } else { Join-Path $HOME '.hubbleflow' }

Write-Host ""
Write-Host "Removing hubbleflow" -ForegroundColor White
Write-Host ""

# Release context caches first - storage bills by the hour, and after the tool
# is gone there is no way left to clean them up.
if (Get-Command hubbleflow -ErrorAction SilentlyContinue) {
    hubbleflow --purge-caches 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "released any active context caches" }
    else { Warn "couldn't reach the caching API (nothing may have been cached)" }
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv tool uninstall hubbleflow 2>&1 | Out-Null
    Ok "removed the hubbleflow command"
} else {
    Warn "uv not found - remove hubbleflow however you installed it"
}

if ($Purge) {
    if (Test-Path $ConfigDir) {
        Write-Host ""
        Write-Host "  About to delete $ConfigDir" -ForegroundColor Yellow
        Write-Host "  This removes your API keys, saved sessions, history and skills."
        $reply = Read-Host "  Type 'delete' to confirm"
        if ($reply -eq 'delete') { Remove-Item -Recurse -Force $ConfigDir; Ok "deleted $ConfigDir" }
        else { Warn "left $ConfigDir alone" }
    } else { Ok "no $ConfigDir to remove" }
} else {
    Ok "kept $ConfigDir - re-run with -Purge to delete it"
}

Write-Host ""
Write-Host "Done." -ForegroundColor White
Write-Host ""
