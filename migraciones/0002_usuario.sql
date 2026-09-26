-- Usuario que hizo la petición, tomado de la cabecera X-Usuario que envía la aplicación ($upstream_http_x_usuario).
-- Queda en NULL para servicios que no la envían, estáticos y peticiones sin autenticar.

ALTER TABLE nginx_acceso ADD COLUMN usuario TEXT;
-- Parcial: la mayoría de las filas no tienen usuario
CREATE INDEX nginx_acceso_usuario_fecha_idx ON nginx_acceso (usuario, fecha) WHERE usuario IS NOT NULL;
