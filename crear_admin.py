"""Ejecutar localmente para preparar sesiones y crear el primer administrador."""
from getpass import getpass

from sqlalchemy import text

from auth import hash_password
from main import engine


def main():
    nombres = input("Nombres: ").strip()
    apellidos = input("Apellidos: ").strip()
    correo = input("Correo: ").strip().lower()
    password = getpass("Contrasena (minimo 8 caracteres): ")
    confirmation = getpass("Repite la contrasena: ")
    if not nombres or not apellidos or "@" not in correo:
        raise SystemExit("Completa nombres, apellidos y un correo valido.")
    if len(nombres) > 100 or len(apellidos) > 100 or len(correo) > 254:
        raise SystemExit("Los datos superan la longitud permitida.")
    if not 8 <= len(password) <= 1024 or password != confirmation:
        raise SystemExit("La contrasena debe coincidir y tener entre 8 y 1024 caracteres.")
    password_hash = hash_password(password)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sesiones (
                token_hash CHAR(64) PRIMARY KEY,
                usuario_id BIGINT NOT NULL REFERENCES usuarios(id),
                creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expira_en TIMESTAMPTZ NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON sesiones(usuario_id)"))
        if conn.execute(text("SELECT id FROM usuarios WHERE LOWER(correo) = :correo"), {"correo": correo}).first():
            raise SystemExit("Ese correo ya existe. No se modifico su cuenta.")
        conn.execute(text("INSERT INTO roles(nombre) VALUES ('administrador') ON CONFLICT (nombre) DO NOTHING"))
        conn.execute(text("""
            INSERT INTO usuarios (rol_id, nombres, apellidos, correo, password_hash)
            VALUES ((SELECT id FROM roles WHERE nombre = 'administrador'),
                    :nombres, :apellidos, :correo, :password_hash)
        """), {"nombres": nombres, "apellidos": apellidos, "correo": correo, "password_hash": password_hash})
    print("Administrador creado y tabla de sesiones preparada.")


if __name__ == "__main__":
    main()
