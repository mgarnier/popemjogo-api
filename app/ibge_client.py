from typing import Any

import httpx


IBGE_BASE_URL = "https://servicodados.ibge.gov.br/api"
POPULATION_AGGREGATE_ID = 6579
POPULATION_VARIABLE_ID = 9324


class IbgeServiceError(Exception):
    pass


async def _get_json(path: str) -> Any:
    try:
        async with httpx.AsyncClient(base_url=IBGE_BASE_URL, timeout=20) as client:
            response = await client.get(path)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise IbgeServiceError("Não foi possível consultar o IBGE.") from error


async def get_regions() -> list[dict[str, Any]]:
    payload = await _get_json("/v1/localidades/regioes")
    if not isinstance(payload, list) or any(
        not isinstance(region, dict)
        or not str(region.get("id", "")).isdigit()
        or not isinstance(region.get("sigla"), str)
        or not isinstance(region.get("nome"), str)
        for region in payload
    ):
        raise IbgeServiceError("O IBGE retornou regiões em formato inesperado.")
    return payload


async def get_states() -> list[dict[str, Any]]:
    payload = await _get_json("/v1/localidades/estados")
    if not isinstance(payload, list) or any(
        not isinstance(state, dict)
        or not str(state.get("id", "")).isdigit()
        or not isinstance(state.get("sigla"), str)
        or not isinstance(state.get("nome"), str)
        or not isinstance(state.get("regiao"), dict)
        or not str(state["regiao"].get("id", "")).isdigit()
        for state in payload
    ):
        raise IbgeServiceError("O IBGE retornou estados em formato inesperado.")
    return payload


async def get_latest_municipal_population() -> list[dict[str, Any]]:
    path = (
        f"/v3/agregados/{POPULATION_AGGREGATE_ID}"
        f"/periodos/-1/variaveis/{POPULATION_VARIABLE_ID}?localidades=N6"
    )
    payload = await _get_json(path)
    try:
        return payload[0]["resultados"][0]["series"]
    except (IndexError, KeyError, TypeError) as error:
        raise IbgeServiceError("O IBGE retornou dados populacionais em formato inesperado.") from error


def latest_population_value(series: dict[str, Any]) -> tuple[int, int] | None:
    if not isinstance(series, dict) or not isinstance(series.get("serie"), dict):
        return None

    values = series["serie"]
    available_periods = [period for period in values if isinstance(period, str) and period.isdigit()]
    if not available_periods:
        return None

    reference_year = max(available_periods, key=int)
    raw_population = values[reference_year]
    if not isinstance(raw_population, str) or not raw_population.isdigit():
        return None

    population = int(raw_population)
    if population <= 0:
        return None
    return population, int(reference_year)


async def choose_municipality(
    scope: str,
    scope_id: int | None,
) -> dict[str, Any]:
    states = await get_states()
    states_by_id = {int(state["id"]): state for state in states}

    if scope == "estado" and scope_id not in states_by_id:
        raise ValueError("Estado não encontrado no IBGE.")

    if scope == "regiao" and scope_id not in {
        int(state["regiao"]["id"]) for state in states
    }:
        raise ValueError("Região não encontrada no IBGE.")

    population_series = await get_latest_municipal_population()
    candidates = []
    for item in population_series:
        if not isinstance(item, dict):
            continue
        locality = item.get("localidade")
        if not isinstance(locality, dict) or not isinstance(locality.get("nome"), str):
            continue
        municipality_id = str(locality.get("id", ""))
        if len(municipality_id) != 7 or not municipality_id[:2].isdigit():
            continue

        state = states_by_id.get(int(municipality_id[:2]))
        if state is None:
            continue
        if scope == "estado" and int(state["id"]) != scope_id:
            continue
        if scope == "regiao" and int(state["regiao"]["id"]) != scope_id:
            continue

        population_data = latest_population_value(item)
        if population_data is None:
            continue

        population, reference_year = population_data
        candidates.append(
            {
                "municipality_id": municipality_id,
                "municipality_name": locality["nome"],
                "state_sigla": state["sigla"],
                "population": population,
                "reference_year": reference_year,
            }
        )

    if not candidates:
        raise IbgeServiceError("Não há municípios com população disponível para esse recorte.")

    import secrets

    return secrets.choice(candidates)