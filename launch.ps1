$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $project '.venv'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    py -3.14 -m venv $venv
}
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install -r (Join-Path $project 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $python (Join-Path $project 'app.py')

