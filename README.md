# Lantano

Logs de nginx → PostgreSQL.

`leer_log_nginx.py` vigila los logs de nginx de forma continua y guarda accesos y errores en PostgreSQL.
Arranca desde el final de los archivos (no carga lo anterior), sigue la rotación de logrotate y, si la base
de datos no responde, se detiene y reintenta sin perder la posición.

## Despliegue

Instalación, verificación, actualización y reversión en producción: [DESPLIEGUE.md](DESPLIEGUE.md).

## Funcionamiento

- La posición (inode + byte) de cada archivo se guarda en `nginx_posicion`, por servidor (`NGINX_SERVIDOR`), en
  la misma transacción que los registros: tras un reinicio continúa exactamente donde iba.
- Rotación: detecta el cambio de inode, sigue leyendo el archivo rotado 5 s y luego pasa al nuevo. Si el
  servicio estaba parado durante una rotación, retoma desde `archivo.log.1` (requiere `delaycompress`, que es
  el valor por defecto de `/etc/logrotate.d/nginx` en Ubuntu).
- Caída de la base de datos: deja de leer y reintenta cada 5, 10, 30 y 60 s. Lo que no se alcanzó a guardar
  se lee de nuevo desde los archivos. Solo se pierde información si logrotate comprime o elimina el archivo
  antes de que vuelva la conexión (con la configuración por defecto, más de un día sin base de datos).
- Filas que PostgreSQL rechaza por sus datos se guardan en `nginx_linea_invalida` sin detener el servicio.
- La URI de cada acceso se guarda separada en `ruta` (sin query) y `parametros` (lo que va después del `?`),
  para buscar y agrupar por endpoint con igualdad en lugar de `LIKE`.
- `api_key` es solo el prefijo de la cabecera `X-API-Key` (`erp_<hex>`), extraído en nginx; si llegara la llave
  completa, se descarta lo que va después del punto. Es el que envió el cliente: con `status` 401 era inválida.
- `ip` es la IP de la conexión: en los sitios detrás de Cloudflare, el nodo de Cloudflare. `ip_real` es la IP del
  cliente, que nginx toma de `CF-Connecting-IP` solo si la conexión viene de un rango de Cloudflare (`real_ip`, ver
  [DESPLIEGUE.md](DESPLIEGUE.md)); en las conexiones directas es igual a `ip`. Es `NULL` en las filas anteriores al
  cambio y en los servidores que aún tienen el `log_format` anterior. Desde ese cambio, `client` de `nginx_error`
  también es la IP del cliente (salvo errores previos a leer la petición, como los del handshake TLS).
- Las fechas del error log no traen zona horaria: se interpretan con la zona del servidor nginx.

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
SELECT host, count(*) AS total, count(*) FILTER (WHERE status >= 500) AS errores_5xx
FROM nginx_acceso WHERE fecha > now() - interval '24 hours'
GROUP BY host ORDER BY total DESC;

-- Endpoints más lentos
SELECT host, ruta, count(*), round(avg(request_time), 3) AS promedio
FROM nginx_acceso WHERE fecha > now() - interval '7 days'
GROUP BY 1, 2 HAVING count(*) > 10 ORDER BY promedio DESC LIMIT 20;

-- Un endpoint en la última hora (usa el índice ruta, fecha)
SELECT fecha, host, parametros, status, request_time FROM nginx_acceso
WHERE ruta = '/api/pagos' AND fecha > now() - interval '1 hour'
ORDER BY fecha DESC;

-- Uso por API Key en las últimas 24 h (401 = llave inválida o expirada)
SELECT api_key, count(*) AS total, count(*) FILTER (WHERE status = 401) AS rechazadas
FROM nginx_acceso WHERE api_key IS NOT NULL AND fecha > now() - interval '24 hours'
GROUP BY api_key ORDER BY total DESC;

-- IPs de clientes con más 404
SELECT ip_real, count(*) FROM nginx_acceso
WHERE status = 404 AND ip_real IS NOT NULL AND fecha > now() - interval '24 hours'
GROUP BY ip_real ORDER BY 2 DESC LIMIT 20;

-- Últimos errores 5xx (usa el índice parcial de errores)
SELECT fecha, host, ruta, status, ip_real FROM nginx_acceso
WHERE status >= 500 AND fecha > now() - interval '1 hour'
ORDER BY fecha DESC;

-- Actividad de una IP en la última hora (usa el índice ip_real, fecha)
SELECT fecha, host, metodo, ruta, status, api_key FROM nginx_acceso
WHERE ip_real = '190.1.2.3' AND fecha > now() - interval '1 hour'
ORDER BY fecha DESC;

-- Conexiones directas a un sitio detrás de Cloudflare (escáneres, o rangos de Cloudflare desactualizados)
SELECT ip, count(*) FROM nginx_acceso
WHERE host = 'api.semanticaapi.com.co' AND ip_real = ip AND fecha > now() - interval '24 hours'
GROUP BY ip ORDER BY 2 DESC;

-- Últimos errores de nginx
SELECT fecha, nivel, server, mensaje, request FROM nginx_error ORDER BY fecha DESC LIMIT 50;
```
