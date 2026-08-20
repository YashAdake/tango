# PowerShell launcher. Works from any directory.
$TangoHome = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $TangoHome ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "Tango's virtualenv is missing. From $TangoHome run:" -ForegroundColor Yellow
    Write-Host '  uv venv --python 3.12; .venv\Scripts\Activate.ps1; uv pip install -e ".[dev]"'
    exit 1
}
& $Py -m tango.cli --db (Join-Path $TangoHome "data\tango.db") `
    --playbooks (Join-Path $TangoHome "playbooks") `
    --hosts (Join-Path $TangoHome "hosts") @args
