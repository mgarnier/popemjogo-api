import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import ibge_client, main


PLAYER_HEADERS = {"X-Player-Id": "0c92dca3-24ca-46ce-a468-584de5ec47ba"}
OTHER_PLAYER_HEADERS = {"X-Player-Id": "355ecaa0-96ed-4b64-91b3-897ba3fa04e9"}
REGIONS = [
    {"id": 1, "sigla": "N", "nome": "Norte"},
    {"id": 3, "sigla": "SE", "nome": "Sudeste"},
]
STATES = [
    {"id": 11, "sigla": "RO", "nome": "Rondônia", "regiao": {"id": 1, "sigla": "N", "nome": "Norte"}},
    {"id": 31, "sigla": "MG", "nome": "Minas Gerais", "regiao": {"id": 3, "sigla": "SE", "nome": "Sudeste"}},
    {"id": 33, "sigla": "RJ", "nome": "Rio de Janeiro", "regiao": {"id": 3, "sigla": "SE", "nome": "Sudeste"}},
]
MUNICIPALITY = {
    "municipality_id": "3304557",
    "municipality_name": "Rio de Janeiro",
    "population": 100000,
    "reference_year": 2026,
}


class TrackingConnection(sqlite3.Connection):
    was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="populacao-api-tests-")
        self.database_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.database_patch = patch.object(main, "DATABASE_PATH", self.database_path)
        self.database_patch.start()
        self.regions_patch = patch.object(
            ibge_client,
            "get_regions",
            new=AsyncMock(return_value=REGIONS),
        )
        self.regions_mock = self.regions_patch.start()
        self.states_patch = patch.object(
            ibge_client,
            "get_states",
            new=AsyncMock(return_value=STATES),
        )
        self.states_mock = self.states_patch.start()
        self.municipality_patch = patch.object(
            ibge_client,
            "choose_municipality",
            new=AsyncMock(return_value=MUNICIPALITY),
        )
        self.municipality_mock = self.municipality_patch.start()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.municipality_patch.stop()
        self.states_patch.stop()
        self.regions_patch.stop()
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def create_game(self, scope="nacional", scope_id=None, headers=None):
        payload = {"scope": scope}
        if scope_id is not None:
            payload["scope_id"] = scope_id
        response = self.client.post(
            "/api/v1/partidas",
            headers=headers or PLAYER_HEADERS,
            json=payload,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_health_and_geography_routes(self):
        self.assertEqual(self.client.get("/api/v1/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/api/v1/geografia/regioes").json(), REGIONS)
        self.assertEqual(len(self.client.get("/api/v1/geografia/estados").json()), 3)
        self.assertEqual(
            [state["id"] for state in self.client.get(
                "/api/v1/geografia/estados?region_id=3"
            ).json()],
            [31, 33],
        )

    def test_geography_routes_translate_ibge_failure_to_502(self):
        self.regions_mock.side_effect = ibge_client.IbgeServiceError("IBGE offline")
        response = self.client.get("/api/v1/geografia/regioes")
        self.assertEqual(response.status_code, 502)

        self.regions_mock.side_effect = None
        self.states_mock.side_effect = ibge_client.IbgeServiceError("IBGE offline")
        response = self.client.get("/api/v1/geografia/estados")
        self.assertEqual(response.status_code, 502)

    def test_create_validates_scope_id_and_player_id(self):
        missing_player = self.client.post("/api/v1/partidas", json={"scope": "nacional"})
        self.assertEqual(missing_player.status_code, 422)

        invalid_player = self.client.post(
            "/api/v1/partidas",
            headers={"X-Player-Id": "not-a-uuid"},
            json={"scope": "nacional"},
        )
        self.assertEqual(invalid_player.status_code, 400)

        national_with_id = self.client.post(
            "/api/v1/partidas",
            headers=PLAYER_HEADERS,
            json={"scope": "nacional", "scope_id": 33},
        )
        self.assertEqual(national_with_id.status_code, 422)

        state_without_id = self.client.post(
            "/api/v1/partidas",
            headers=PLAYER_HEADERS,
            json={"scope": "estado"},
        )
        self.assertEqual(state_without_id.status_code, 422)

        negative_scope_id = self.client.post(
            "/api/v1/partidas",
            headers=PLAYER_HEADERS,
            json={"scope": "estado", "scope_id": -1},
        )
        self.assertEqual(negative_scope_id.status_code, 422)

    def test_create_persists_game_but_hides_answer_while_active(self):
        game_id = self.create_game("estado", 33)
        self.municipality_mock.assert_awaited_once_with("estado", 33)

        active_game = self.client.get(f"/api/v1/partidas/{game_id}", headers=PLAYER_HEADERS)
        self.assertEqual(active_game.status_code, 200)
        self.assertEqual(active_game.json()["status"], "active")
        self.assertEqual(active_game.json()["attempts"], 0)
        self.assertNotIn("population", active_game.json())
        self.assertNotIn("municipality_name", active_game.json())

    def test_guess_accepts_both_inclusive_five_percent_boundaries(self):
        for guess in (95000, 105000):
            with self.subTest(guess=guess):
                game_id = self.create_game()
                response = self.client.put(
                    f"/api/v1/partidas/{game_id}/palpites",
                    headers=PLAYER_HEADERS,
                    json={"guess": guess},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["result"], "correct")
                self.assertEqual(response.json()["attempts"], 1)
                self.assertEqual(response.json()["winning_guess"], guess)

    def test_guess_just_outside_tolerance_returns_direction_and_attempt_count(self):
        scenarios = ((94999, "higher"), (105001, "lower"))
        for guess, expected_result in scenarios:
            with self.subTest(guess=guess):
                game_id = self.create_game()
                response = self.client.put(
                    f"/api/v1/partidas/{game_id}/palpites",
                    headers=PLAYER_HEADERS,
                    json={"guess": guess},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"result": expected_result, "attempts": 1})

    def test_attempts_increment_until_winning_guess_and_history_records_winner(self):
        game_id = self.create_game()
        first = self.client.put(
            f"/api/v1/partidas/{game_id}/palpites",
            headers=PLAYER_HEADERS,
            json={"guess": 50000},
        )
        self.assertEqual(first.json(), {"result": "higher", "attempts": 1})

        second = self.client.put(
            f"/api/v1/partidas/{game_id}/palpites",
            headers=PLAYER_HEADERS,
            json={"guess": 100000},
        )
        self.assertEqual(second.json()["attempts"], 2)

        history = self.client.get("/api/v1/historico", headers=PLAYER_HEADERS).json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "won")
        self.assertEqual(history[0]["attempts"], 2)
        self.assertEqual(history[0]["winning_guess"], 100000)
        self.assertEqual(history[0]["population_reference_year"], 2026)

    def test_guess_rejects_negative_values_and_terminal_games(self):
        game_id = self.create_game()
        negative_guess = self.client.put(
            f"/api/v1/partidas/{game_id}/palpites",
            headers=PLAYER_HEADERS,
            json={"guess": -1},
        )
        self.assertEqual(negative_guess.status_code, 422)

        self.client.put(
            f"/api/v1/partidas/{game_id}/palpites",
            headers=PLAYER_HEADERS,
            json={"guess": 100000},
        )
        after_win = self.client.put(
            f"/api/v1/partidas/{game_id}/palpites",
            headers=PLAYER_HEADERS,
            json={"guess": 100000},
        )
        self.assertEqual(after_win.status_code, 409)

        abandoned_id = self.create_game()
        self.client.put(f"/api/v1/partidas/{abandoned_id}/desistencia", headers=PLAYER_HEADERS)
        after_abandon = self.client.put(
            f"/api/v1/partidas/{abandoned_id}/palpites",
            headers=PLAYER_HEADERS,
            json={"guess": 100000},
        )
        self.assertEqual(after_abandon.status_code, 409)

    def test_abandon_reveals_answer_and_preserves_attempt_count(self):
        game_id = self.create_game()
        self.client.put(
            f"/api/v1/partidas/{game_id}/palpites",
            headers=PLAYER_HEADERS,
            json={"guess": 50000},
        )

        response = self.client.put(
            f"/api/v1/partidas/{game_id}/desistencia",
            headers=PLAYER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "abandoned")
        self.assertEqual(response.json()["attempts"], 1)
        self.assertEqual(response.json()["municipality_id"], "3304557")
        self.assertEqual(response.json()["population"], 100000)
        self.assertEqual(response.json()["population_reference_year"], 2026)

    def test_history_and_game_access_are_isolated_by_player(self):
        game_id = self.create_game()

        self.assertEqual(
            self.client.get(f"/api/v1/partidas/{game_id}", headers=OTHER_PLAYER_HEADERS).status_code,
            404,
        )
        self.assertEqual(self.client.get("/api/v1/historico", headers=OTHER_PLAYER_HEADERS).json(), [])

        other_history = self.client.get("/api/v1/historico", headers=OTHER_PLAYER_HEADERS)
        self.assertEqual(other_history.status_code, 200)

    def test_clear_history_deletes_only_the_requesting_players_games(self):
        self.create_game(headers=PLAYER_HEADERS)
        self.create_game(headers=OTHER_PLAYER_HEADERS)

        response = self.client.delete("/api/v1/historico", headers=PLAYER_HEADERS)
        self.assertEqual(response.json(), {"deleted_games": 1})
        self.assertEqual(self.client.get("/api/v1/historico", headers=PLAYER_HEADERS).json(), [])
        self.assertEqual(len(self.client.get("/api/v1/historico", headers=OTHER_PLAYER_HEADERS).json()), 1)

    def test_ibge_failure_does_not_create_a_game(self):
        self.municipality_mock.side_effect = ibge_client.IbgeServiceError("IBGE indisponível")

        response = self.client.post(
            "/api/v1/partidas",
            headers=PLAYER_HEADERS,
            json={"scope": "nacional"},
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.client.get("/api/v1/historico", headers=PLAYER_HEADERS).json(), [])

    def test_clear_history_closes_database_connection(self):
        game_id = self.create_game()
        tracked_connections = []

        def tracked_connect():
            connection = sqlite3.connect(
                self.database_path,
                timeout=10,
                factory=TrackingConnection,
            )
            connection.row_factory = sqlite3.Row
            tracked_connections.append(connection)
            return connection

        with patch.object(main, "connect_database", side_effect=tracked_connect):
            response = self.client.delete("/api/v1/historico", headers=PLAYER_HEADERS)

        self.assertEqual(response.json(), {"deleted_games": 1})
        self.assertTrue(tracked_connections[-1].was_closed)
        self.assertEqual(
            self.client.get(f"/api/v1/partidas/{game_id}", headers=PLAYER_HEADERS).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()