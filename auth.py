"""Autenticacion con contrasenas scrypt y sesiones revocables."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import text


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class Login(BaseModel):
    correo: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


def register_auth(app, engine):
    router = APIRouter(prefix="/auth", tags=["Autenticacion"])
    bearer = HTTPBearer(auto_error=False)
    dummy_hash = hash_password(secrets.token_urlsafe(32))

    def unauthorized():
        return HTTPException(401, "Credenciales invalidas o sesion vencida", headers={"WWW-Authenticate": "Bearer"})

    def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None:
            raise unauthorized()
        token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        with engine.connect() as conn:
            user = conn.execute(text("""
                SELECT u.id, u.nombres, u.apellidos, u.correo, r.nombre AS rol
                FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id
                JOIN roles r ON r.id = u.rol_id
                WHERE s.token_hash = :token AND s.expira_en > CURRENT_TIMESTAMP
                  AND u.activo = TRUE
            """), {"token": token_hash}).mappings().first()
        if user is None:
            raise unauthorized()
        return dict(user)

    @router.post("/login")
    def login(data: Login):
        with engine.begin() as conn:
            user = conn.execute(text("""
                SELECT id, password_hash, activo FROM usuarios
                WHERE LOWER(correo) = :correo FOR UPDATE
            """), {"correo": data.correo.strip().lower()}).mappings().first()
            valid = verify_password(data.password, user["password_hash"] if user else dummy_hash)
            if not valid or user is None or not user["activo"]:
                raise unauthorized()
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(hours=8)
            conn.execute(text("DELETE FROM sesiones WHERE expira_en <= CURRENT_TIMESTAMP"))
            conn.execute(text("""
                INSERT INTO sesiones (token_hash, usuario_id, expira_en)
                VALUES (:token, :usuario, :expira)
            """), {"token": hashlib.sha256(token.encode()).hexdigest(), "usuario": user["id"], "expira": expires})
        return {"access_token": token, "token_type": "bearer", "expires_in": 28800}

    @router.get("/me")
    def me(user=Depends(current_user)):
        return user

    @router.post("/logout")
    def logout(user=Depends(current_user), credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM sesiones WHERE token_hash = :token"),
                         {"token": hashlib.sha256(credentials.credentials.encode()).hexdigest()})
        return {"mensaje": "Sesion cerrada"}

    app.include_router(router)
    return current_user
