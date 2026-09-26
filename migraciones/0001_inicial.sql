-- Esquema inicial: accesos, errores, posiciones de lectura y líneas inválidas

CREATE TABLE nginx_acceso (
    id BIGSERIAL PRIMARY KEY,
    fecha TIMESTAMPTZ NOT NULL,
    archivo TEXT NOT NULL,
    host TEXT,
    ip INET,
    metodo TEXT,
    ruta TEXT,              -- path de $request_uri, sin query
    parametros TEXT,        -- query de $request_uri, sin '?' y con los valores sensibles ocultos
    protocolo TEXT,
    status SMALLINT,
    bytes BIGINT,
    referer TEXT,
    user_agent TEXT,
    request_time NUMERIC(10,3),
    upstream_time TEXT,
    upstream TEXT,
    servidor TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX nginx_acceso_fecha_idx ON nginx_acceso (fecha);
CREATE INDEX nginx_acceso_host_fecha_idx ON nginx_acceso (host, fecha);
CREATE INDEX nginx_acceso_status_idx ON nginx_acceso (status);
CREATE INDEX nginx_acceso_ip_idx ON nginx_acceso (ip);
CREATE INDEX nginx_acceso_ruta_fecha_idx ON nginx_acceso (ruta, fecha);

CREATE TABLE nginx_error (
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
    servidor TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX nginx_error_fecha_idx ON nginx_error (fecha);
CREATE INDEX nginx_error_nivel_fecha_idx ON nginx_error (nivel, fecha);

-- servidor es NGINX_SERVIDOR ('' si está vacío): varios servidores nginx comparten las mismas rutas
CREATE TABLE nginx_posicion (
    servidor TEXT NOT NULL DEFAULT '',
    archivo TEXT NOT NULL,
    inode NUMERIC(20,0) NOT NULL,
    posicion BIGINT NOT NULL,
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (servidor, archivo)
);

CREATE TABLE nginx_linea_invalida (
    id BIGSERIAL PRIMARY KEY,
    archivo TEXT,
    linea TEXT,
    motivo TEXT,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);
