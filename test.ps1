$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Ambiente virtual não encontrado. Execute .\build.ps1 antes de testar o backend."
}

$env:PYTHONPATH = $PSScriptRoot
& $python -m unittest discover -s tests -v
