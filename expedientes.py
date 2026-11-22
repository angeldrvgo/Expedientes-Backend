"""Expedientes y responsables con asignaciones historicas."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


Priority = Literal['baja', 'normal', 'alta', 'urgente']


class CaseData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    identificador: str = Field(min_length=1, max_length=100)
    cliente_id: int = Field(gt=0)
    materia: str = Field(min_length=1, max_length=100)
    organo_competente: str = Field(min_length=1, max_length=200)
    contraparte: str | None = Field(default=None, max_length=200)
    descripcion: str | None = Field(default=None, max_length=10000)
    estado_procesal: str = Field(min_length=1, max_length=100)
    prioridad: Priority = 'normal'


class CreateCase(CaseData):
    responsable_id: int = Field(gt=0)


class AssignResponsible(BaseModel):
    model_config = ConfigDict(extra='forbid')
    responsable_id: int = Field(gt=0)


class ChangeCaseStatus(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    motivo: str = Field(min_length=1, max_length=2000)


class StatusHistoryResponse(BaseModel):
    id: int
    expediente_id: int
    estado_anterior: str
    estado_nuevo: str
    motivo: str
    realizado_por: int
    realizado_en: datetime


class CaseResponse(CaseData):
    id: int
    estado_administrativo: str
    creado_por: int
    creado_en: datetime
    actualizado_en: datetime
    cerrado_en: datetime | None
    responsable_id: int | None
    responsable_nombre: str | None


class AssignmentResponse(BaseModel):
    id: int
    expediente_id: int
    usuario_id: int
    responsable_nombre: str
    asignado_por: int
    fecha_inicio: datetime
    fecha_fin: datetime | None


CASE_SELECT = '''SELECT e.id, e.identificador, e.cliente_id, e.materia, e.organo_competente,
    e.contraparte, e.descripcion, e.estado_procesal, e.prioridad, e.estado_administrativo,
    e.creado_por, e.creado_en, e.actualizado_en, e.cerrado_en,
    a.usuario_id AS responsable_id,
    u.nombres || ' ' || u.apellidos AS responsable_nombre
    FROM expedientes e
    LEFT JOIN asignaciones_responsables a ON a.expediente_id=e.id AND a.fecha_fin IS NULL
    LEFT JOIN usuarios u ON u.id=a.usuario_id'''


def register_cases(app, engine, current_user):
    router = APIRouter(prefix='/expedientes', tags=['Expedientes'])

    def staff(user=Depends(current_user)):
        if user['rol'] not in ('administrador', 'asistente'):
            raise HTTPException(403, 'Solo administradores y asistentes pueden registrar o reasignar expedientes')
        return user

    def visibility(user):
        if user['rol'] in ('administrador', 'asistente'):
            return 'TRUE'
        if user['rol'] == 'abogado':
            return '''EXISTS (SELECT 1 FROM asignaciones_responsables ar
                WHERE ar.expediente_id=e.id AND ar.usuario_id=:usuario_id AND ar.fecha_fin IS NULL)'''
        raise HTTPException(403, 'Rol no autorizado')

    def fetch_case(conn, case_id, user, lock=False):
        # Bloquear primero el expediente serializa ediciones y reasignaciones.
        if lock:
            found = conn.execute(text(f'''SELECT e.id FROM expedientes e
                WHERE e.id=:id AND ({visibility(user)}) FOR UPDATE OF e'''),
                {'id': case_id, 'usuario_id': user['id']}).first()
            if found is None:
                raise HTTPException(404, 'Expediente no encontrado o sin acceso')
        result = conn.execute(text(CASE_SELECT + f' WHERE e.id=:id AND ({visibility(user)})'),
                              {'id': case_id, 'usuario_id': user['id']}).mappings().first()
        if result is None:
            raise HTTPException(404, 'Expediente no encontrado o sin acceso')
        return dict(result)

    def validate_client(conn, client_id):
        if conn.execute(text('SELECT id FROM clientes WHERE id=:id'), {'id': client_id}).first() is None:
            raise HTTPException(422, 'El cliente no existe')

    def validate_responsible(conn, user_id):
        row = conn.execute(text('''SELECT u.id FROM usuarios u JOIN roles r ON r.id=u.rol_id
            WHERE u.id=:id AND u.activo=TRUE AND r.nombre='abogado' FOR SHARE OF u'''), {'id': user_id}).first()
        if row is None:
            raise HTTPException(422, 'El responsable debe ser un abogado activo')

    @router.get('/responsables-disponibles')
    def available_responsibles(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(staff)):
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(text('''SELECT u.id, u.nombres, u.apellidos
                FROM usuarios u JOIN roles r ON r.id=u.rol_id
                WHERE u.activo=TRUE AND r.nombre='abogado' ORDER BY u.id LIMIT :limit OFFSET :offset'''),
                {'limit': limit, 'offset': offset}).mappings()]

    @router.get('', response_model=list[CaseResponse])
    def list_cases(q: str | None = Query(None, min_length=1, max_length=200),
                   cliente_id: int | None = Query(None, gt=0),
                   responsable_id: int | None = Query(None, gt=0),
                   prioridad: Priority | None = None,
                   estado_administrativo: Literal['abierto', 'cerrado', 'archivado'] | None = None,
                   incluir_archivados: bool = False,
                   limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                   user=Depends(current_user)):
        conditions = [visibility(user)]
        params = {'usuario_id': user['id'], 'limit': limit, 'offset': offset}
        if estado_administrativo is None and not incluir_archivados:
            conditions.append("e.estado_administrativo <> 'archivado'")
        for name, value, column in [('cliente_id', cliente_id, 'e.cliente_id'),
                                     ('responsable_id', responsable_id, 'a.usuario_id'),
                                     ('prioridad', prioridad, 'e.prioridad'),
                                     ('estado_administrativo', estado_administrativo, 'e.estado_administrativo')]:
            if value is not None:
                conditions.append(f'{column}=:{name}')
                params[name] = value
        if q is not None:
            conditions.append('''(strpos(lower(e.identificador), :q)>0
                OR strpos(lower(e.materia), :q)>0)''')
            params['q'] = q.strip().lower()
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(CASE_SELECT + ' WHERE '
                + ' AND '.join(f'({c})' for c in conditions)
                + ' ORDER BY e.id DESC LIMIT :limit OFFSET :offset'), params).mappings()]

    @router.post('', response_model=CaseResponse, status_code=201)
    def create_case(data: CreateCase, user=Depends(staff)):
        try:
            with engine.begin() as conn:
                validate_client(conn, data.cliente_id)
                validate_responsible(conn, data.responsable_id)
                values = data.model_dump(exclude={'responsable_id'})
                values['creado_por'] = user['id']
                case_id = conn.execute(text('''INSERT INTO expedientes
                    (identificador,cliente_id,materia,organo_competente,contraparte,descripcion,
                     estado_procesal,prioridad,creado_por)
                    VALUES (:identificador,:cliente_id,:materia,:organo_competente,:contraparte,
                            :descripcion,:estado_procesal,:prioridad,:creado_por) RETURNING id'''), values).scalar_one()
                conn.execute(text('''INSERT INTO asignaciones_responsables(expediente_id,usuario_id,asignado_por)
                    VALUES (:expediente,:responsable,:actor)'''),
                    {'expediente': case_id, 'responsable': data.responsable_id, 'actor': user['id']})
                return fetch_case(conn, case_id, user)
        except IntegrityError:
            raise HTTPException(409, 'No se pudo registrar; comprueba el identificador y los datos relacionados') from None

    @router.get('/{case_id}', response_model=CaseResponse)
    def read_case(case_id: int, user=Depends(current_user)):
        with engine.connect() as conn:
            return fetch_case(conn, case_id, user)

    @router.put('/{case_id}', response_model=CaseResponse)
    def update_case(case_id: int, data: CaseData, user=Depends(current_user)):
        try:
            with engine.begin() as conn:
                old = fetch_case(conn, case_id, user, lock=True)
                if old['estado_administrativo'] != 'abierto':
                    raise HTTPException(409, 'Solo se pueden editar expedientes abiertos')
                if user['rol'] == 'abogado' and data.cliente_id != old['cliente_id']:
                    raise HTTPException(403, 'El cambio de cliente corresponde al personal de registro')
                validate_client(conn, data.cliente_id)
                values = data.model_dump()
                values['id'] = case_id
                conn.execute(text('''UPDATE expedientes SET identificador=:identificador,cliente_id=:cliente_id,
                    materia=:materia,organo_competente=:organo_competente,contraparte=:contraparte,
                    descripcion=:descripcion,estado_procesal=:estado_procesal,prioridad=:prioridad,
                    actualizado_en=clock_timestamp() WHERE id=:id'''), values)
                return fetch_case(conn, case_id, user)
        except IntegrityError:
            raise HTTPException(409, 'No se pudo actualizar; comprueba el identificador y los datos relacionados') from None

    @router.put('/{case_id}/responsable', response_model=CaseResponse)
    def assign_responsible(case_id: int, data: AssignResponsible, user=Depends(staff)):
        with engine.begin() as conn:
            old = fetch_case(conn, case_id, user, lock=True)
            if old['estado_administrativo'] != 'abierto':
                raise HTTPException(409, 'Solo se pueden reasignar expedientes abiertos')
            validate_responsible(conn, data.responsable_id)
            if old['responsable_id'] == data.responsable_id:
                return old
            timestamp = conn.execute(text('SELECT clock_timestamp()')).scalar_one()
            conn.execute(text('''UPDATE asignaciones_responsables SET fecha_fin=:fecha
                WHERE expediente_id=:id AND fecha_fin IS NULL'''), {'fecha': timestamp, 'id': case_id})
            conn.execute(text('''INSERT INTO asignaciones_responsables
                (expediente_id,usuario_id,asignado_por,fecha_inicio)
                VALUES (:id,:responsable,:actor,:fecha)'''),
                {'id': case_id, 'responsable': data.responsable_id, 'actor': user['id'], 'fecha': timestamp})
            conn.execute(text('UPDATE expedientes SET actualizado_en=:fecha WHERE id=:id'), {'id': case_id, 'fecha': timestamp})
            return fetch_case(conn, case_id, user)

    @router.get('/{case_id}/responsables', response_model=list[AssignmentResponse])
    def assignment_history(case_id: int, user=Depends(current_user)):
        with engine.connect() as conn:
            fetch_case(conn, case_id, user)
            return [dict(row) for row in conn.execute(text('''SELECT a.id,a.expediente_id,a.usuario_id,
                u.nombres || ' ' || u.apellidos AS responsable_nombre,a.asignado_por,a.fecha_inicio,a.fecha_fin
                FROM asignaciones_responsables a JOIN usuarios u ON u.id=a.usuario_id
                WHERE a.expediente_id=:id ORDER BY a.fecha_inicio,a.id'''), {'id': case_id}).mappings()]

    def change_status(case_id, data, user, target, required):
        if user['rol'] != 'abogado':
            raise HTTPException(403, 'Solo el abogado responsable puede cerrar o archivar expedientes')
        with engine.begin() as conn:
            old = fetch_case(conn, case_id, user, lock=True)
            if old['responsable_id'] != user['id']:
                raise HTTPException(404, 'Expediente no encontrado o sin acceso')
            if old['estado_administrativo'] == target:
                return old
            if old['estado_administrativo'] != required:
                raise HTTPException(409, f'El expediente debe estar {required} para pasar a {target}')
            timestamp = conn.execute(text('SELECT clock_timestamp()')).scalar_one()
            conn.execute(text('''UPDATE expedientes SET estado_administrativo=:estado,
                cerrado_en=:cierre, actualizado_en=:fecha WHERE id=:id'''),
                {'estado': target, 'cierre': timestamp if target == 'cerrado' else old['cerrado_en'],
                 'fecha': timestamp, 'id': case_id})
            conn.execute(text('''INSERT INTO historial_estados_expedientes
                (expediente_id, estado_anterior, estado_nuevo, motivo, realizado_por, realizado_en)
                VALUES (:id,:anterior,:nuevo,:motivo,:actor,:fecha)'''),
                {'id': case_id, 'anterior': required, 'nuevo': target,
                 'motivo': data.motivo, 'actor': user['id'], 'fecha': timestamp})
            return fetch_case(conn, case_id, user)

    @router.post('/{case_id}/cerrar', response_model=CaseResponse)
    def close_case(case_id: int, data: ChangeCaseStatus, user=Depends(current_user)):
        return change_status(case_id, data, user, 'cerrado', 'abierto')

    @router.post('/{case_id}/archivar', response_model=CaseResponse)
    def archive_case(case_id: int, data: ChangeCaseStatus, user=Depends(current_user)):
        return change_status(case_id, data, user, 'archivado', 'cerrado')

    @router.get('/{case_id}/historial-estados', response_model=list[StatusHistoryResponse])
    def status_history(case_id: int, limit: int = Query(50, ge=1, le=100),
                       offset: int = Query(0, ge=0), user=Depends(current_user)):
        with engine.connect() as conn:
            fetch_case(conn, case_id, user)
            return [dict(row) for row in conn.execute(text('''SELECT id, expediente_id,
                estado_anterior, estado_nuevo, motivo, realizado_por, realizado_en
                FROM historial_estados_expedientes WHERE expediente_id=:id
                ORDER BY realizado_en, id LIMIT :limit OFFSET :offset'''),
                {'id': case_id, 'limit': limit, 'offset': offset}).mappings()]

    app.include_router(router)
