"""Administracion de usuarios. Los hashes nunca se incluyen en respuestas."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auth import hash_password


class UserData(BaseModel):
    nombres: str = Field(min_length=1, max_length=100)
    apellidos: str = Field(min_length=1, max_length=100)
    correo: str = Field(min_length=3, max_length=254)
    rol: Literal['administrador', 'abogado', 'asistente']

    @field_validator('nombres', 'apellidos', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('correo')
    @classmethod
    def validate_email(cls, value):
        value = value.strip().lower()
        if value.count('@') != 1 or any(c.isspace() for c in value):
            raise ValueError('Correo invalido')
        local, domain = value.split('@')
        if not local or not domain or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
            raise ValueError('Correo invalido')
        return value


class CreateUser(UserData):
    password: str = Field(min_length=8, max_length=1024)


class UserStatus(BaseModel):
    activo: bool


class UserResponse(UserData):
    id: int
    activo: bool


USER_SELECT = '''SELECT u.id, u.nombres, u.apellidos, u.correo, r.nombre AS rol, u.activo
                 FROM usuarios u JOIN roles r ON r.id = u.rol_id'''


def register_users(app, engine, current_user):
    def admin_only(user=Depends(current_user)):
        if user['rol'] != 'administrador':
            raise HTTPException(403, 'Se requiere el rol administrador')
        return user

    router = APIRouter(prefix='/usuarios', tags=['Usuarios'], dependencies=[Depends(admin_only)])

    def role_id(conn, name):
        result = conn.execute(text('SELECT id FROM roles WHERE nombre = :nombre'), {'nombre': name}).scalar_one_or_none()
        if result is None:
            raise HTTPException(422, 'El rol no esta configurado en la base de datos')
        return result

    def get_user(conn, user_id):
        row = conn.execute(text(USER_SELECT + ' WHERE u.id = :id'), {'id': user_id}).mappings().first()
        if row is None:
            raise HTTPException(404, 'Usuario no encontrado')
        return dict(row)

    @router.get('', response_model=list[UserResponse])
    def list_users(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(text(USER_SELECT + ' ORDER BY u.id LIMIT :limit OFFSET :offset'),
                                                 {'limit': limit, 'offset': offset}).mappings()]

    @router.post('', response_model=UserResponse, status_code=201)
    def create_user(data: CreateUser):
        values = data.model_dump(exclude={'password', 'rol'})
        values['password_hash'] = hash_password(data.password)
        try:
            with engine.begin() as conn:
                values['rol_id'] = role_id(conn, data.rol)
                user_id = conn.execute(text('''
                    INSERT INTO usuarios(nombres, apellidos, correo, rol_id, password_hash)
                    VALUES (:nombres, :apellidos, :correo, :rol_id, :password_hash) RETURNING id
                '''), values).scalar_one()
                return get_user(conn, user_id)
        except IntegrityError:
            raise HTTPException(409, 'No se pudo crear el usuario; comprueba si el correo ya existe') from None

    @router.get('/{user_id}', response_model=UserResponse)
    def read_user(user_id: int):
        with engine.connect() as conn:
            return get_user(conn, user_id)

    @router.put('/{user_id}', response_model=UserResponse)
    def update_user(user_id: int, data: UserData, admin=Depends(admin_only)):
        if user_id == admin['id'] and data.rol != 'administrador':
            raise HTTPException(409, 'No puedes quitarte tu propio rol de administrador')
        try:
            with engine.begin() as conn:
                old = get_user(conn, user_id)
                values = data.model_dump(exclude={'rol'})
                values.update(id=user_id, rol_id=role_id(conn, data.rol))
                conn.execute(text('''UPDATE usuarios SET nombres=:nombres, apellidos=:apellidos,
                                    correo=:correo, rol_id=:rol_id WHERE id=:id'''), values)
                if old['rol'] != data.rol:
                    conn.execute(text('DELETE FROM sesiones WHERE usuario_id=:id'), {'id': user_id})
                return get_user(conn, user_id)
        except IntegrityError:
            raise HTTPException(409, 'No se pudo actualizar el usuario; comprueba si el correo ya existe') from None

    @router.patch('/{user_id}/estado', response_model=UserResponse)
    def set_status(user_id: int, data: UserStatus, admin=Depends(admin_only)):
        if user_id == admin['id'] and not data.activo:
            raise HTTPException(409, 'No puedes desactivar tu propia cuenta')
        with engine.begin() as conn:
            get_user(conn, user_id)
            conn.execute(text('UPDATE usuarios SET activo=:activo WHERE id=:id'), {'activo': data.activo, 'id': user_id})
            if not data.activo:
                conn.execute(text('DELETE FROM sesiones WHERE usuario_id=:id'), {'id': user_id})
            return get_user(conn, user_id)

    app.include_router(router)
