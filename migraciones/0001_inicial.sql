-- Esquema inicial: accesos, errores, posiciones de lectura y líneas inválidas, para nginx y Apache

CREATE TABLE acceso (
    id BIGSERIAL PRIMARY KEY,
    origen TEXT NOT NULL CHECK (origen IN ('nginx', 'apache')),  -- LANTANO_ORIGEN del servicio que la guardó
    fecha TIMESTAMPTZ NOT NULL,
    archivo TEXT NOT NULL,
    host TEXT,
    ip INET,                -- IP de la conexión: en los sitios detrás de Cloudflare, el nodo de Cloudflare
    ip_real INET,           -- IP del cliente (CF-Connecting-IP solo si la conexión viene de un rango de Cloudflare)
    metodo TEXT,
    ruta TEXT,              -- path de la URI, sin query
    parametros TEXT,        -- query de la URI, sin '?' y con los valores sensibles ocultos
    protocolo TEXT,
    status SMALLINT,
    bytes BIGINT,
    referer TEXT,
    user_agent TEXT,
    request_time NUMERIC(10,3),  -- segundos
    upstream_time TEXT,     -- solo nginx
    upstream TEXT,          -- solo nginx
    api_key TEXT,           -- prefijo de la cabecera X-API-Key (erp_<hex>), nunca el secreto
    servidor TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX acceso_fecha_idx ON acceso (fecha);
CREATE INDEX acceso_host_fecha_idx ON acceso (host, fecha);
CREATE INDEX acceso_ruta_fecha_idx ON acceso (ruta, fecha);
CREATE INDEX acceso_ip_fecha_idx ON acceso (ip, fecha);
-- Parciales: la mayoría de las filas son 2xx, sin API Key
CREATE INDEX acceso_errores_fecha_idx ON acceso (fecha) WHERE status >= 400;
CREATE INDEX acceso_ip_real_fecha_idx ON acceso (ip_real, fecha) WHERE ip_real IS NOT NULL;
CREATE INDEX acceso_api_key_fecha_idx ON acceso (api_key, fecha) WHERE api_key IS NOT NULL;

CREATE TABLE error (
    id BIGSERIAL PRIMARY KEY,
    origen TEXT NOT NULL CHECK (origen IN ('nginx', 'apache')),
    fecha TIMESTAMPTZ NOT NULL,
    archivo TEXT NOT NULL,
    nivel TEXT,
    modulo TEXT,            -- solo Apache (core, proxy, ssl, php, ...)
    pid INTEGER,
    tid BIGINT,
    cid BIGINT,             -- solo nginx
    mensaje TEXT,
    client INET,
    server TEXT,            -- solo nginx
    request TEXT,           -- solo nginx
    upstream TEXT,          -- solo nginx
    host TEXT,              -- solo nginx
    referer TEXT,
    servidor TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX error_fecha_idx ON error (fecha);
CREATE INDEX error_nivel_fecha_idx ON error (nivel, fecha);

-- servidor es LANTANO_SERVIDOR ('' si está vacío): varios servidores comparten las mismas rutas
CREATE TABLE posicion (
    servidor TEXT NOT NULL DEFAULT '',
    archivo TEXT NOT NULL,
    inode NUMERIC(20,0) NOT NULL,
    posicion BIGINT NOT NULL,
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (servidor, archivo)
);

CREATE TABLE linea_invalida (
    id BIGSERIAL PRIMARY KEY,
    archivo TEXT,
    linea TEXT,
    motivo TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
