"""Registro de clientes y consultas limitadas por responsabilidad."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


class ClientData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    tipo_persona: Literal['natural', 'juridica']
    nombre_completo_razon_social: str = Field(min_length=1, max_length=200)
    tipo_documento: str | None = Field(default=None, min_length=1, max_length=20)
    numero_documento: str | None = Field(default=None, min_length=1, max_length=30)
    telefono: str | None = Field(default=None, max_length=30)
    correo: str | None = Field(default=None, max_length=254)

    @field_validator('tipo_documento')
    @classmethod
    def normalize_type(cls, value):
        return value.upper() if value is not None else None

    @field_validator('correo')
    @classmethod
    def validate_email(cls, value):
        if value is None:
            return None
        value = value.lower()
        if value.count('@') != 1 or any(c.isspace() for c in value):
            raise ValueError('Correo invalido')
        local, domain = value.split('@')
        if not local or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
            raise ValueError('Correo invalido')
        return value

    @model_validator(mode='after')
    def document_pair(self):
        if (self.tipo_documento is None) != (self.numero_documento is None):
            raise ValueError('Indica tipo y numero de documento juntos, o deja ambos en null')
        return self


class ClientResponse(ClientData):
    id: int


CLIENT_COLUMNS = '''c.id, c.tipo_persona, c.nombre_completo_razon_social,
                   c.tipo_documento, c.numero_documento, c.telefono, c.correo'''


def register_clients(app, engine, current_user):
    router = APIRouter(prefix='/clientes', tags=['Clientes'])

    def staff(user=Depends(current_user)):
        if user['rol'] not in ('administrador', 'asistente'):
            raise HTTPException(403, 'Solo administradores y asistentes pueden gestionar clientes')
        return user

    def visibility(user):
        if user['rol'] in ('administrador', 'asistente'):
            return 'TRUE'
        if user['rol'] == 'abogado':
            return '''EXISTS (
                SELECT 1 FROM expedientes e JOIN asignaciones_responsables a ON a.expediente_id=e.id
                WHERE e.cliente_id=c.id AND a.usuario_id=:usuario_id AND a.fecha_fin IS NULL
            )'''
        raise HTTPException(403, 'Rol no autorizado')

    def fetch_client(conn, client_id, user):
        row = conn.execute(text(f'''SELECT {CLIENT_COLUMNS} FROM clientes c
                                   WHERE c.id=:id AND ({visibility(user)})'''),
                           {'id': client_id, 'usuario_id': user['id']}).mappings().first()
        if row is None:
            raise HTTPException(404, 'Cliente no encontrado o sin acceso')
        return dict(row)

    @router.get('', response_model=list[ClientResponse])
    def list_clients(q: str | None = Query(None, min_length=1, max_length=200),
                     limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                     user=Depends(current_user)):
        conditions = visibility(user)
        params = {'usuario_id': user['id'], 'limit': limit, 'offset': offset}
        if q is not None:
            conditions += ''' AND (strpos(lower(c.nombre_completo_razon_social), :q)>0
                              OR strpos(lower(COALESCE(c.numero_documento, '')), :q)>0)'''
            params['q'] = q.strip().lower()
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(text(f'''SELECT {CLIENT_COLUMNS} FROM clientes c
                WHERE {conditions} ORDER BY c.id LIMIT :limit OFFSET :offset'''), params).mappings()]

    @router.post('', response_model=ClientResponse, status_code=201)
    def create_client(data: ClientData, user=Depends(staff)):
        try:
            with engine.begin() as conn:
                client_id = conn.execute(text('''INSERT INTO clientes
                    (tipo_persona, nombre_completo_razon_social, tipo_documento, numero_documento, telefono, correo)
                    VALUES (:tipo_persona, :nombre_completo_razon_social, :tipo_documento,
                            :numero_documento, :telefono, :correo) RETURNING id'''), data.model_dump()).scalar_one()
                return fetch_client(conn, client_id, user)
        except IntegrityError:
            raise HTTPException(409, 'No se pudo registrar el cliente; comprueba si su documento ya existe') from None

    @router.get('/{client_id}', response_model=ClientResponse)
    def read_client(client_id: int, user=Depends(current_user)):
        with engine.connect() as conn:
            return fetch_client(conn, client_id, user)

    @router.put('/{client_id}', response_model=ClientResponse)
    def update_client(client_id: int, data: ClientData, user=Depends(staff)):
        try:
            with engine.begin() as conn:
                fetch_client(conn, client_id, user)
                values = data.model_dump()
                values['id'] = client_id
                conn.execute(text('''UPDATE clientes SET tipo_persona=:tipo_persona,
                    nombre_completo_razon_social=:nombre_completo_razon_social,
                    tipo_documento=:tipo_documento, numero_documento=:numero_documento,
                    telefono=:telefono, correo=:correo WHERE id=:id'''), values)
                return fetch_client(conn, client_id, user)
        except IntegrityError:
            raise HTTPException(409, 'No se pudo actualizar el cliente; comprueba si su documento ya existe') from None

    app.include_router(router)
