-- Tablas para leer_log_nginx.py
--
-- Requisitos (se crean a mano antes):
--   CREATE DATABASE <base>;
--   CREATE USER <usuario> WITH PASSWORD '...';
--
-- Ejecutar conectado a la base de datos, con un usuario que pueda crear tablas:
--   psql -h <host> -U <admin> -d <base> -v usuario=<usuario> -f crear_tablas.sql
--
-- Es seguro ejecutarlo varias veces.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS nginx_acceso (
    id BIGSERIAL PRIMARY KEY,
    fecha TIMESTAMPTZ NOT NULL,
    archivo TEXT NOT NULL,
    host TEXT,
    ip INET,
    metodo TEXT,
    uri TEXT,
    protocolo TEXT,
    status SMALLINT,
    bytes BIGINT,
    referer TEXT,
    user_agent TEXT,
    request_time NUMERIC(10,3),
    upstream_time TEXT,
    upstream TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS nginx_acceso_fecha_idx ON nginx_acceso (fecha);
CREATE INDEX IF NOT EXISTS nginx_acceso_host_fecha_idx ON nginx_acceso (host, fecha);
CREATE INDEX IF NOT EXISTS nginx_acceso_status_idx ON nginx_acceso (status);
CREATE INDEX IF NOT EXISTS nginx_acceso_ip_idx ON nginx_acceso (ip);

CREATE TABLE IF NOT EXISTS nginx_error (
    id BIGSERIAL PRIMARY KEY,
    fecha TIMESTAMPTZ NOT NULL,
    archivo TEXT NOT NULL,
    nivel TEXT,
    pid INTEGER,
    tid BIGINT,
    cid BIGINT,
    mensaje TEXT,
    client INET,
    server TEXT,
    request TEXT,
    upstream TEXT,
    host TEXT,
    referer TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS nginx_error_fecha_idx ON nginx_error (fecha);
CREATE INDEX IF NOT EXISTS nginx_error_nivel_fecha_idx ON nginx_error (nivel, fecha);

CREATE TABLE IF NOT EXISTS nginx_posicion (
    archivo TEXT PRIMARY KEY,
    inode NUMERIC(20,0) NOT NULL,
    posicion BIGINT NOT NULL,
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nginx_linea_invalida (
    id BIGSERIAL PRIMARY KEY,
    archivo TEXT,
    linea TEXT,
    motivo TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Permisos mínimos para el usuario del servicio
GRANT SELECT, INSERT ON nginx_acceso, nginx_error, nginx_linea_invalida TO :"usuario";
GRANT SELECT, INSERT, UPDATE ON nginx_posicion TO :"usuario";
GRANT USAGE ON SEQUENCE nginx_acceso_id_seq, nginx_error_id_seq, nginx_linea_invalida_id_seq TO :"usuario";

COMMIT;
