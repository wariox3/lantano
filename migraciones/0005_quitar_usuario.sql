-- Se deja de registrar el usuario (cabecera X-Usuario): no se usaba. Borra la columna y sus datos; el índice
-- nginx_acceso_usuario_fecha_idx se elimina con ella.

ALTER TABLE nginx_acceso DROP COLUMN usuario;
