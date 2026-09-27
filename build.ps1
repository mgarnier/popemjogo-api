$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker não foi encontrado no PATH. Instale o Docker Desktop antes de compilar o backend."
}

$compose = Join-Path (Split-Path $PSScriptRoot) "mvp-front\docker-compose.yml"
docker compose -f $compose build backend
