$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker não foi encontrado no PATH. Instale o Docker Desktop antes de testar o backend."
}

$compose = Join-Path (Split-Path $PSScriptRoot) "mvp-front\docker-compose.yml"
if (-not (Test-Path $compose)) { throw "Clone o frontend ao lado do backend para encontrar docker-compose.yml." }

docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker Compose não está disponível. Verifique a instalação do Docker Desktop." }

$dockerOS = docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $dockerOS -ne "linux") { throw "Inicie o Docker Desktop em modo de containers Linux." }

docker compose -f $compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Verifique a configuração do Compose no frontend." }

docker compose -f $compose --profile test run --build --rm backend-test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
