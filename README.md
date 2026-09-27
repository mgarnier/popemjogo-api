# População em Jogo - API

API do jogo **População em Jogo**, responsável por consultar dados do IBGE, sortear municípios, avaliar palpites e persistir partidas no SQLite.

A API é consumida pelo frontend React e não expõe o catálogo do IBGE diretamente ao navegador.

## Tecnologias

- Python para implementar a API.
- FastAPI para definir as rotas REST.
- Uvicorn para servir a API.
- HTTPX para consultar o IBGE.
- SQLite para persistir as partidas.
- unittest para testar a API.

## Pré-requisitos

Para usar os scripts auxiliares e a execução integrada, instale Docker Desktop com Docker Compose disponível.

## Execução local sem Docker

Para executar diretamente no host, crie um ambiente virtual e instale as dependências:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Para iniciar a API:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Scripts auxiliares com Docker

Os scripts PowerShell da raiz usam o `docker-compose.yml` mantido no repositório frontend:

```powershell
.\build.ps1
.\run.ps1
.\test.ps1
```

Eles constroem a imagem, iniciam o backend e executam os testes no profile `test`.

## Execução com Docker Compose

A configuração integrada fica no repositório frontend, que deve estar clonado lado a lado:

```text
mvp-arq/
  mvp-front/
  mvp-back/
```

A partir de `mvp-front`:

```powershell
docker compose build backend
docker compose up -d backend
docker compose ps
```

Ou, para executar frontend e backend juntos:

```powershell
docker compose up -d
```

A API fica disponível em:

- Healthcheck: <http://localhost:8000/api/v1/health>
- Swagger: <http://localhost:8000/docs>
- OpenAPI: <http://localhost:8000/openapi.json>

O SQLite do container fica em `/app/data/populacao_em_jogo.sqlite3` e é persistido no volume nomeado `sqlite_data`.

## API

Todas as operações por jogador exigem o header:

```text
X-Player-Id: UUID
```

### Saúde e geografia

- `GET /api/v1/health`: verifica se a API está ativa e o banco foi inicializado.
- `GET /api/v1/geografia/regioes`: lista regiões obtidas do IBGE.
- `GET /api/v1/geografia/estados`: lista estados obtidos do IBGE.
- `GET /api/v1/geografia/estados?region_id=3`: filtra estados de uma região.

### Partidas

- `POST /api/v1/partidas`: cria uma partida e sorteia um município.
- `GET /api/v1/partidas/{game_id}`: retorna a partida do jogador.
- `PUT /api/v1/partidas/{game_id}/palpites`: registra um palpite.
- `PUT /api/v1/partidas/{game_id}/desistencia`: encerra e revela a partida.

Exemplo de criação:

```json
{
  "scope": "estado",
  "scope_id": 33
}
```

A criação retorna o identificador, escopo, status, cidade e sigla da UF. A população e o ano de referência permanecem ocultos enquanto a partida está ativa.

Um palpite é considerado correto quando satisfaz:

```text
abs(palpite - população) * 100 <= população * 5
```

O limite de +/-5% é inclusivo. Palpites fora da margem retornam `higher` ou `lower` e incrementam o número de tentativas.

### Histórico

- `GET /api/v1/historico`: lista as partidas do UUID informado.
- `DELETE /api/v1/historico`: remove todo o histórico desse UUID.

O histórico inclui município, população, ano de referência, tentativas, palpite vencedor e status da partida encerrada.

## IBGE

O backend consulta os serviços públicos do IBGE:

- Localidades: `https://servicodados.ibge.gov.br/api/v1/localidades/regioes`
- Estados: `https://servicodados.ibge.gov.br/api/v1/localidades/estados`
- População municipal: `https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/-1/variaveis/9324?localidades=N6`

A cada nova partida, a API consulta dados atuais, filtra o escopo escolhido e sorteia um município. O catálogo de localidades e o lote populacional do IBGE não são armazenados como cache no banco.

A aplicação persiste somente o retrato usado pela partida: código e nome do município, população, ano de referência, escopo, tentativas e timestamps. Isso é necessário para preservar o histórico e não representa um catálogo geral do IBGE.

Falhas de comunicação ou dados inválidos são convertidas em respostas de erro apropriadas, sem criar uma partida incompleta.

## Persistência e limitações

- O schema SQLite é criado automaticamente na inicialização.
- O volume Compose preserva o histórico após reinício do backend.
- `docker compose down` preserva o volume.
- `docker compose down -v` remove o banco persistido.
- O UUID anônimo não é autenticação: qualquer pessoa que obtenha o valor pode fazer requisições em nome daquele identificador.
- Não há contas, login, ranking, multiplayer ou autenticação de usuários.

## Testes

Executar localmente ou pelo script:

```powershell
.\test.ps1
```

Os testes usam IBGE mockado e SQLite temporário. Cobrem parsing e filtragem de dados do IBGE, rotas, validação de escopo, isolamento por jogador, limite de tolerância, estados da partida, histórico e fechamento das conexões.

## Repositório relacionado

O frontend está em <https://github.com/mgarnier/popemjogo-front> e contém o `docker-compose.yml`, o proxy Nginx e as instruções de execução integrada.
