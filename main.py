import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from auth import register_auth
from usuarios import register_users
from clientes import register_clients
from expedientes import register_cases


logger = logging.getLogger("uvicorn.error")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).with_name(".env"),
        env_file_encoding="utf-8",
    )

    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str


settings = Settings()

database_url = URL.create(
    drivername="postgresql+psycopg",
    username=settings.db_user,
    password=settings.db_password,
    host=settings.db_host,
    port=settings.db_port,
    database=settings.db_name,
)

engine = create_engine(
    database_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5},
)

app = FastAPI(title="Sistema de seguimiento de expedientes")
current_user = register_auth(app, engine)
register_users(app, engine, current_user)
register_clients(app, engine, current_user)
register_cases(app, engine, current_user)


@app.get("/")
def inicio():
    return {"mensaje": "API de expedientes funcionando"}


@app.get("/salud/bd")
def comprobar_base_datos():
    try:
        with engine.connect() as conexion:
            conexion.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Error al conectar con PostgreSQL")
        raise HTTPException(
            status_code=503,
            detail="No se pudo conectar con la base de datos",
        ) from None

    return {"estado": "ok", "base_datos": settings.db_name}
