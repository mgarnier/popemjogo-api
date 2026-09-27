import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import ibge_client


DATABASE_PATH = Path(
    os.getenv(
        "DATABASE_PATH",
        str(Path(__file__).resolve().parent.parent / "data" / "populacao_em_jogo.sqlite3"),
    )
)


class NewGameRequest(BaseModel):
    scope: Literal["nacional", "regiao", "estado"] = "nacional"
    scope_id: Optional[int] = Field(default=None, ge=1)


class GuessRequest(BaseModel):
    guess: int = Field(ge=0)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_database() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def database_connection():
    connection = connect_database()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database() -> None:
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
                id TEXT PRIMARY KEY,
                player_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                scope_id INTEGER,
                municipality_id TEXT NOT NULL,
                municipality_name TEXT NOT NULL,
                population INTEGER NOT NULL,
                population_reference_year INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL CHECK (status IN ('active', 'won', 'abandoned')),
                attempts INTEGER NOT NULL DEFAULT 0,
                winning_guess INTEGER
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_games_player_created "
            "ON games (player_id, created_at DESC)"
        )


def player_id_from_header(value: str = Header(alias="X-Player-Id")) -> str:
    try:
        return str(UUID(value))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="X-Player-Id deve ser um UUID válido.") from error


def game_to_response(row: sqlite3.Row, include_answer: bool = False) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "scope": row["scope"],
        "scope_id": row["scope_id"],
        "status": row["status"],
        "attempts": row["attempts"],
        "created_at": row["created_at"],
        "ended_at": row["ended_at"],
    }
    if include_answer or row["status"] != "active":
        result.update(
            {
                "municipality_id": row["municipality_id"],
                "municipality_name": row["municipality_name"],
                "population": row["population"],
                "population_reference_year": row["population_reference_year"],
                "winning_guess": row["winning_guess"],
            }
        )
    return result


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="População em Jogo API",
    description="API do jogo de adivinhação de população com dados do IBGE.",
    version="0.1.0",
    lifespan=lifespan,
)

allowed_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Player-Id"],
)


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/geografia/regioes")
async def list_regions() -> list[dict[str, Any]]:
    try:
        regions = await ibge_client.get_regions()
    except ibge_client.IbgeServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return regions


@app.get("/api/v1/geografia/estados")
async def list_states(
    region_id: Optional[int] = Query(default=None, ge=1),
) -> list[dict[str, Any]]:
    try:
        states = await ibge_client.get_states()
    except ibge_client.IbgeServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if region_id is not None:
        states = [state for state in states if int(state["regiao"]["id"]) == region_id]
    return states


@app.post("/api/v1/partidas", status_code=status.HTTP_201_CREATED)
async def create_game(
    request: NewGameRequest,
    player_id: str = Header(alias="X-Player-Id"),
) -> dict[str, Any]:
    player_id = player_id_from_header(player_id)
    if request.scope == "nacional" and request.scope_id is not None:
        raise HTTPException(status_code=422, detail="O escopo nacional não recebe scope_id.")
    if request.scope != "nacional" and request.scope_id is None:
        raise HTTPException(status_code=422, detail="scope_id é obrigatório para esse escopo.")

    try:
        municipality = await ibge_client.choose_municipality(request.scope, request.scope_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ibge_client.IbgeServiceError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    game_id = str(uuid4())
    created_at = now_utc()
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO games (
                id, player_id, scope, scope_id, municipality_id, municipality_name,
                population, population_reference_year, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                game_id,
                player_id,
                request.scope,
                request.scope_id,
                municipality["municipality_id"],
                municipality["municipality_name"],
                municipality["population"],
                municipality["reference_year"],
                created_at,
            ),
        )

    return {
        "id": game_id,
        "scope": request.scope,
        "scope_id": request.scope_id,
        "municipality_name": municipality["municipality_name"],
        "state_sigla": municipality["state_sigla"],
        "status": "active",
        "attempts": 0,
        "created_at": created_at,
    }


@app.get("/api/v1/partidas/{game_id}")
async def get_game(
    game_id: str,
    player_id: str = Header(alias="X-Player-Id"),
) -> dict[str, Any]:
    player_id = player_id_from_header(player_id)
    with database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM games WHERE id = ? AND player_id = ?",
            (game_id, player_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Partida não encontrada.")
    return game_to_response(row)


@app.put("/api/v1/partidas/{game_id}/palpites")
async def submit_guess(
    game_id: str,
    request: GuessRequest,
    player_id: str = Header(alias="X-Player-Id"),
) -> dict[str, Any]:
    player_id = player_id_from_header(player_id)
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM games WHERE id = ? AND player_id = ?",
            (game_id, player_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Partida não encontrada.")
        if row["status"] != "active":
            raise HTTPException(status_code=409, detail="A partida já foi encerrada.")

        attempts = row["attempts"] + 1
        is_winner = abs(request.guess - row["population"]) * 100 <= row["population"] * 5
        ended_at = now_utc() if is_winner else None
        connection.execute(
            """
            UPDATE games
            SET attempts = ?, status = ?, ended_at = ?, winning_guess = ?
            WHERE id = ? AND player_id = ?
            """,
            (
                attempts,
                "won" if is_winner else "active",
                ended_at,
                request.guess if is_winner else None,
                game_id,
                player_id,
            ),
        )

    if is_winner:
        return {
            "result": "correct",
            "attempts": attempts,
            "municipality_name": row["municipality_name"],
            "population": row["population"],
            "population_reference_year": row["population_reference_year"],
            "winning_guess": request.guess,
        }
    return {
        "result": "higher" if row["population"] > request.guess else "lower",
        "attempts": attempts,
    }


@app.put("/api/v1/partidas/{game_id}/desistencia")
async def abandon_game(
    game_id: str,
    player_id: str = Header(alias="X-Player-Id"),
) -> dict[str, Any]:
    player_id = player_id_from_header(player_id)
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM games WHERE id = ? AND player_id = ?",
            (game_id, player_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Partida não encontrada.")
        if row["status"] != "active":
            raise HTTPException(status_code=409, detail="A partida já foi encerrada.")
        ended_at = now_utc()
        connection.execute(
            "UPDATE games SET status = 'abandoned', ended_at = ? WHERE id = ? AND player_id = ?",
            (ended_at, game_id, player_id),
        )

    return {
        "status": "abandoned",
        "attempts": row["attempts"],
        "municipality_id": row["municipality_id"],
        "municipality_name": row["municipality_name"],
        "population": row["population"],
        "population_reference_year": row["population_reference_year"],
    }


@app.get("/api/v1/historico")
async def list_history(
    player_id: str = Header(alias="X-Player-Id"),
) -> list[dict[str, Any]]:
    player_id = player_id_from_header(player_id)
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM games WHERE player_id = ? ORDER BY created_at DESC",
            (player_id,),
        ).fetchall()
    return [game_to_response(row, include_answer=True) for row in rows]


@app.delete("/api/v1/historico")
async def clear_history(
    player_id: str = Header(alias="X-Player-Id"),
) -> dict[str, int]:
    player_id = player_id_from_header(player_id)
    with database_connection() as connection:
        cursor = connection.execute("DELETE FROM games WHERE player_id = ?", (player_id,))
    return {"deleted_games": cursor.rowcount}