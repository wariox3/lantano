-- Prefijo de la API Key de la petición (erp_<hex>), extraído en nginx de la cabecera X-API-Key con un map. Nunca
-- se guarda el secreto. Es el prefijo que envió el cliente, no uno validado: status 401 indica llave inválida.

ALTER TABLE nginx_acceso ADD COLUMN api_key TEXT;
-- Parcial: la mayoría de las filas no tienen API Key
CREATE INDEX nginx_acceso_api_key_fecha_idx ON nginx_acceso (api_key, fecha) WHERE api_key IS NOT NULL;
