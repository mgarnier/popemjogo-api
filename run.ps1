$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Ambiente virtual não encontrado. Execute .\build.ps1 antes de executar o backend."
}

& $python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
