# Lantano

Logs de nginx o Apache → PostgreSQL.

`leer_log.py` vigila los logs de nginx o de Apache de forma continua y guarda accesos y errores en PostgreSQL
(base `bdlantano`, usuario `lantano`). Arranca desde el final de los archivos (no carga lo anterior), sigue la
rotación de logrotate y, si la base de datos no responde, se detiene y reintenta sin perder la posición.

## Despliegue

Instalación, verificación, actualización y reversión en producción: [DESPLIEGUE.md](DESPLIEGUE.md).

## Funcionamiento

- `LANTANO_ORIGEN` (`nginx` o `apache`) indica qué servidor web se lee: define las rutas por defecto de los logs
  (`/var/log/nginx` o `/var/log/apache2`) y el formato del log de errores. Se guarda en la columna `origen` de
  `acceso` y `error`, así que servidores nginx y Apache pueden escribir en la misma base. Un servicio lee un solo
  origen.
- El log de acceso se escribe en JSON con las mismas claves en los dos servidores: `log_format json_log` en nginx y
  `LogFormat ... json_log` en Apache ([lantano-apache.conf](lantano-apache.conf)). Diferencias de Apache:
  - La URI sale de la línea de petición (`%r`), sin decodificar, igual que `$request_uri` de nginx.
  - `request_time` llega en microsegundos (`%D`) y se guarda en segundos.
  - Los bytes no imprimibles o no ASCII que Apache escribe como `\xhh` se convierten a texto (UTF-8).
  - `upstream` y `upstream_time` quedan en `NULL`.
- El log de errores se lee en el formato por defecto de cada uno. En Apache se guarda el módulo (`core`, `proxy`,
  `ssl`, `php`, ...) en `modulo`, y la IP de `[client ...]` en `client`; `cid`, `server`, `request`, `upstream` y
  `host` son solo de nginx.
- Las aplicaciones Symfony que escriben sus logs en `stderr` (Monolog en JSON) terminan en el log de errores del
  servidor web. Esas líneas también se guardan en `error`: `nivel` es el `level_name` en minúsculas (`debug`,
  `warning`, `critical`, ...), `modulo` es `symfony.<channel>` (`symfony.request`, `symfony.cache`, ...) y
  `mensaje` es el `message`, más la clase y el archivo de la excepción si la hay.
- La posición (inode + byte) de cada archivo se guarda en `posicion`, por servidor (`LANTANO_SERVIDOR`), en
  la misma transacción que los registros: tras un reinicio continúa exactamente donde iba.
- Rotación: detecta el cambio de inode, sigue leyendo el archivo rotado 5 s y luego pasa al nuevo. Si el
  servicio estaba parado durante una rotación, retoma desde `archivo.log.1` (requiere `delaycompress`, que es
  el valor por defecto de `/etc/logrotate.d/nginx` y `/etc/logrotate.d/apache2` en Ubuntu).
- Caída de la base de datos: deja de leer y reintenta cada 5, 10, 30 y 60 s. Lo que no se alcanzó a guardar
  se lee de nuevo desde los archivos. Solo se pierde información si logrotate comprime o elimina el archivo
  antes de que vuelva la conexión (con la configuración por defecto, más de un día sin base de datos).
- Las líneas que no se pueden interpretar y las filas que PostgreSQL rechaza por sus datos se guardan en
  `linea_invalida` sin detener el servicio.
- La URI de cada acceso se guarda separada en `ruta` (sin query) y `parametros` (lo que va después del `?`),
  para buscar y agrupar por endpoint con igualdad en lugar de `LIKE`.
- `api_key` es solo el prefijo de la cabecera `X-API-Key` (`erp_<hex>`), extraído por el servidor web; si llegara
  la llave completa, se descarta lo que va después del punto. Es el que envió el cliente: con `status` 401 era
  inválida.
- `ip` es la IP de la conexión: en los sitios detrás de Cloudflare, el nodo de Cloudflare. `ip_real` es la IP del
  cliente, que nginx toma de `CF-Connecting-IP` solo si la conexión viene de un rango de Cloudflare (`real_ip`,
  ver [DESPLIEGUE.md](DESPLIEGUE.md)); en las conexiones directas es igual a `ip`. Los servidores Apache no están
  detrás de Cloudflare: en ellos `ip_real` es siempre igual a `ip`. Es `NULL` en los servidores nginx que aún
  tienen un `log_format` sin ese campo. `client` de `error` también es la IP del cliente (salvo errores previos a
  leer la petición, como los del handshake TLS).
- Las fechas del log de errores no traen zona horaria: se interpretan con la zona del servidor web.

## Desarrollo local

Base `bdlantano` con dueño `lantano`, igual que en producción (como administrador de PostgreSQL):

```sql
CREATE USER lantano WITH PASSWORD '<clave>';
CREATE DATABASE bdlantano OWNER lantano;
```

```bash
cp .env.example .env          # completar LANTANO_PG_DATABASE_CLAVE y LANTANO_PG_DATABASE_HOST=localhost,
                              # y LANTANO_PG_SSLMODE=prefer si el PostgreSQL local no tiene SSL
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python migrar.py
venv/bin/python leer_log.py --debug
```

Para leer los logs locales, el usuario debe estar en el grupo `adm` (`sudo usermod -aG adm $USER` y volver a
iniciar sesión), o apuntar `LANTANO_ACCESS_GLOB` / `LANTANO_ERROR_GLOB` a archivos de prueba.

## Migraciones

El esquema está en `migraciones/`, un archivo `NNNN_descripcion.sql` por cambio, y `migrar.py` aplica los que
faltan (registro en `lantano_migracion`). Para cambiar el esquema:

1. Crear `migraciones/<siguiente número>_<descripcion>.sql` con SQL plano (sin `BEGIN`/`COMMIT` ni comandos de
   `psql`: cada archivo ya corre en su propia transacción, así que tampoco admite `CREATE INDEX CONCURRENTLY`).
2. Nunca modificar una migración ya desplegada: los cambios siempre van en una nueva.

El servicio exige que la base esté en la última migración de su código, así que el mismo commit debe traer la
migración y el código que la usa.

## Consultas de ejemplo

```sql
-- Peticiones y errores 5xx por proyecto, últimas 24 h
SELECT origen, host, count(*) AS total, count(*) FILTER (WHERE status >= 500) AS errores_5xx
FROM acceso WHERE fecha > now() - interval '24 hours'
GROUP BY origen, host ORDER BY total DESC;

-- Endpoints más lentos
SELECT host, ruta, count(*), round(avg(request_time), 3) AS promedio
FROM acceso WHERE fecha > now() - interval '7 days'
GROUP BY 1, 2 HAVING count(*) > 10 ORDER BY promedio DESC LIMIT 20;

-- Un endpoint en la última hora (usa el índice ruta, fecha)
SELECT fecha, host, parametros, status, request_time FROM acceso
WHERE ruta = '/api/pagos' AND fecha > now() - interval '1 hour'
ORDER BY fecha DESC;

-- Uso por API Key en las últimas 24 h (401 = llave inválida o expirada)
SELECT api_key, count(*) AS total, count(*) FILTER (WHERE status = 401) AS rechazadas
FROM acceso WHERE api_key IS NOT NULL AND fecha > now() - interval '24 hours'
GROUP BY api_key ORDER BY total DESC;

-- IPs de clientes con más 404
SELECT ip_real, count(*) FROM acceso
WHERE status = 404 AND ip_real IS NOT NULL AND fecha > now() - interval '24 hours'
GROUP BY ip_real ORDER BY 2 DESC LIMIT 20;

-- Últimos errores 5xx (usa el índice parcial de errores)
SELECT fecha, origen, host, ruta, status, ip_real FROM acceso
WHERE status >= 500 AND fecha > now() - interval '1 hour'
ORDER BY fecha DESC;

-- Actividad de una IP en la última hora (usa el índice ip_real, fecha)
SELECT fecha, host, metodo, ruta, status, api_key FROM acceso
WHERE ip_real = '190.1.2.3' AND fecha > now() - interval '1 hour'
ORDER BY fecha DESC;

-- Conexiones directas a un sitio detrás de Cloudflare (escáneres, o rangos de Cloudflare desactualizados)
SELECT ip, count(*) FROM acceso
WHERE host = 'api.semanticaapi.com.co' AND ip_real = ip AND fecha > now() - interval '24 hours'
GROUP BY ip ORDER BY 2 DESC;

-- Últimos errores de nginx
SELECT fecha, nivel, server, mensaje, request FROM error WHERE origen = 'nginx' ORDER BY fecha DESC LIMIT 50;

-- Últimos errores de Apache (sin los notice/info de arranque y parada)
SELECT fecha, nivel, modulo, client, mensaje FROM error
WHERE origen = 'apache' AND nivel NOT IN ('notice', 'info') AND coalesce(modulo, '') NOT LIKE 'symfony.%' ORDER BY fecha DESC LIMIT 50;

-- Errores de las aplicaciones Symfony (excepciones no capturadas: nivel critical, módulo symfony.request)
SELECT fecha, servidor, nivel, modulo, mensaje FROM error
WHERE modulo LIKE 'symfony.%' AND nivel IN ('error', 'critical', 'alert', 'emergency') ORDER BY fecha DESC LIMIT 50;
```
