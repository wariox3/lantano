-- ip se consulta siempre con rango de fechas: (ip, fecha) reemplaza a (ip). status solo interesa para errores:
-- un índice parcial de 4xx/5xx reemplaza a (status), que casi no filtra porque la mayoría de las filas son 2xx.

CREATE INDEX nginx_acceso_ip_fecha_idx ON nginx_acceso (ip, fecha);
-- Parcial: solo errores. Lo usan las consultas con status >= 400, >= 500, = 404, etc.
CREATE INDEX nginx_acceso_errores_fecha_idx ON nginx_acceso (fecha) WHERE status >= 400;
DROP INDEX nginx_acceso_ip_idx;
DROP INDEX nginx_acceso_status_idx;
