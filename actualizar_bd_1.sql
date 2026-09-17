-- Actualización 1: columna servidor en nginx_acceso y nginx_error
--
-- Guarda el nombre del servidor nginx que generó el registro (variable NGINX_SERVIDOR del .env).
-- Los registros anteriores quedan con servidor en NULL.
--
-- Ejecutar ANTES de actualizar el código, conectado a la base de datos, con el dueño de las tablas:
--   psql -h <host> -U <admin> -d <base> -f actualizar_bd_1.sql
--
-- Es seguro ejecutarlo varias veces.

\set ON_ERROR_STOP on

BEGIN;

ALTER TABLE nginx_acceso ADD COLUMN IF NOT EXISTS servidor TEXT;
ALTER TABLE nginx_error ADD COLUMN IF NOT EXISTS servidor TEXT;

COMMIT;
