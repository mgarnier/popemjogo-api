import unittest
from unittest.mock import AsyncMock, patch

from app import ibge_client


STATES = [
    {"id": 11, "sigla": "RO", "nome": "Rondônia", "regiao": {"id": 1, "sigla": "N", "nome": "Norte"}},
    {"id": 31, "sigla": "MG", "nome": "Minas Gerais", "regiao": {"id": 3, "sigla": "SE", "nome": "Sudeste"}},
    {"id": 33, "sigla": "RJ", "nome": "Rio de Janeiro", "regiao": {"id": 3, "sigla": "SE", "nome": "Sudeste"}},
]


def population_row(municipality_id: str, name: str, *values: tuple[str, str]):
    return {
        "localidade": {"id": municipality_id, "nome": name},
        "serie": dict(values),
    }


POPULATION_SERIES = [
    population_row("3304557", "Rio de Janeiro - RJ", ("2024", "..."), ("2026", "100000")),
    population_row("3300100", "Angra dos Reis - RJ", ("2026", "50000")),
    population_row("3106200", "Belo Horizonte - MG", ("2026", "200000")),
    population_row("1100015", "Alta Floresta D'Oeste - RO", ("2026", "3000")),
    population_row("3309999", "Sem população - RJ", ("2026", "...")),
    population_row("bad-code", "Código inválido", ("2026", "1200")),
]


class LatestPopulationValueTests(unittest.TestCase):
    def test_selects_latest_numeric_period_and_parses_population(self):
        row = population_row(
            "3304557",
            "Rio de Janeiro - RJ",
            ("2024", "6747815"),
            ("2026", "6731133"),
            ("2025", "6730729"),
        )

        self.assertEqual(ibge_client.latest_population_value(row), (6731133, 2026))

    def test_ignores_missing_non_numeric_or_non_positive_population(self):
        for value in ("...", "X", "", "0", "-15"):
            with self.subTest(value=value):
                row = population_row("3304557", "Rio de Janeiro - RJ", ("2026", value))
                self.assertIsNone(ibge_client.latest_population_value(row))

    def test_returns_none_when_no_year_is_available(self):
        self.assertIsNone(ibge_client.latest_population_value({"serie": {"P1": "100"}}))

    def test_returns_none_for_malformed_series(self):
        for row in ({}, {"serie": []}, {"serie": {2026: "100000"}}):
            with self.subTest(row=row):
                self.assertIsNone(ibge_client.latest_population_value(row))


class GeographyPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_malformed_regions_payload(self):
        with patch.object(ibge_client, "_get_json", new=AsyncMock(return_value=[{"id": 1}])):
            with self.assertRaisesRegex(ibge_client.IbgeServiceError, "regiões"):
                await ibge_client.get_regions()

    async def test_rejects_malformed_states_payload(self):
        with patch.object(ibge_client, "_get_json", new=AsyncMock(return_value=[{"id": 33, "sigla": "RJ"}])):
            with self.assertRaisesRegex(ibge_client.IbgeServiceError, "estados"):
                await ibge_client.get_states()


class ChooseMunicipalityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.states_patch = patch.object(
            ibge_client,
            "get_states",
            new=AsyncMock(return_value=STATES),
        )
        self.population_patch = patch.object(
            ibge_client,
            "get_latest_municipal_population",
            new=AsyncMock(return_value=POPULATION_SERIES),
        )
        self.states_mock = self.states_patch.start()
        self.population_mock = self.population_patch.start()
        self.choice_patch = patch("secrets.choice", side_effect=lambda rows: rows[0])
        self.choice_mock = self.choice_patch.start()

    async def asyncTearDown(self):
        self.choice_patch.stop()
        self.population_patch.stop()
        self.states_patch.stop()

    async def test_national_scope_includes_all_supported_municipalities(self):
        selected = await ibge_client.choose_municipality("nacional", None)

        self.assertEqual(selected["municipality_id"], "3304557")
        self.assertEqual(selected["state_sigla"], "RJ")
        self.assertEqual(selected["population"], 100000)
        self.assertEqual(selected["reference_year"], 2026)
        self.assertEqual(
            [row["municipality_id"] for row in self.choice_mock.call_args.args[0]],
            ["3304557", "3300100", "3106200", "1100015"],
        )

    async def test_state_scope_filters_by_municipality_code_prefix(self):
        await ibge_client.choose_municipality("estado", 31)

        candidates = self.choice_mock.call_args.args[0]
        self.assertEqual([row["municipality_id"] for row in candidates], ["3106200"])

    async def test_region_scope_includes_each_state_in_that_region(self):
        await ibge_client.choose_municipality("regiao", 3)

        candidates = self.choice_mock.call_args.args[0]
        self.assertEqual(
            [row["municipality_id"] for row in candidates],
            ["3304557", "3300100", "3106200"],
        )

    async def test_unknown_state_fails_before_population_request(self):
        with self.assertRaisesRegex(ValueError, "Estado não encontrado"):
            await ibge_client.choose_municipality("estado", 99)

        self.population_mock.assert_not_awaited()

    async def test_unknown_region_fails_before_population_request(self):
        with self.assertRaisesRegex(ValueError, "Região não encontrada"):
            await ibge_client.choose_municipality("regiao", 99)

        self.population_mock.assert_not_awaited()

    async def test_no_eligible_population_raises_service_error(self):
        self.population_mock.return_value = [
            population_row("3309999", "Sem população - RJ", ("2026", "...")),
            population_row("bad-code", "Código inválido", ("2026", "1200")),
        ]

        with self.assertRaisesRegex(ibge_client.IbgeServiceError, "Não há municípios"):
            await ibge_client.choose_municipality("estado", 33)


if __name__ == "__main__":
    unittest.main()