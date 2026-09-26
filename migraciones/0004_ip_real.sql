-- IP del cliente: $remote_addr después de real_ip (CF-Connecting-IP solo si la conexión viene de un rango de
-- Cloudflare). ip sigue siendo la IP de la conexión ($realip_remote_addr). NULL en filas anteriores a este cambio
-- y en servidores con el log_format anterior.

ALTER TABLE nginx_acceso ADD COLUMN ip_real INET;
-- Parcial: las filas anteriores al cambio quedan en NULL
CREATE INDEX nginx_acceso_ip_real_fecha_idx ON nginx_acceso (ip_real, fecha) WHERE ip_real IS NOT NULL;
