CREATE TABLE IF NOT EXISTS historial_estados_expedientes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    expediente_id BIGINT NOT NULL REFERENCES expedientes(id),
    estado_anterior VARCHAR(20) NOT NULL,
    estado_nuevo VARCHAR(20) NOT NULL,
    motivo TEXT NOT NULL,
    realizado_por BIGINT NOT NULL REFERENCES usuarios(id),
    realizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (estado_anterior IN ('abierto', 'cerrado', 'archivado')),
    CHECK (estado_nuevo IN ('abierto', 'cerrado', 'archivado')),
    CHECK (estado_anterior <> estado_nuevo)
);

CREATE INDEX IF NOT EXISTS idx_historial_estados_expediente
    ON historial_estados_expedientes(expediente_id, realizado_en, id);
