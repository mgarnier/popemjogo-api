$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Test-Path $python)) {
    throw "Python não foi encontrado no PATH. Instale o Python antes de compilar o backend."
}

if (-not (Test-Path $python)) {
    python -m venv .venv
}

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt
& $python -m compileall app
