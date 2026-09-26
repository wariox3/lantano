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
- `usuario` viene de la cabecera `X-Usuario` que envía la aplicación (ver el `log_format` en
  [DESPLIEGUE.md](DESPLIEGUE.md)). Es `NULL` en los servicios que no la envían.
- Las fechas del error log no traen zona horaria: se interpretan con la zona del servidor nginx.

## Migraciones

El esquema está en `migraciones/`, un archivo `NNNN_descripcion.sql` por cambio, y `migrar.py` aplica los que
faltan (registro en `lantano_migracion`). Para cambiar el esquema:

1. Crear `migraciones/<siguiente número>_<descripcion>.sql` con SQL plano (sin `BEGIN`/`COMMIT` ni comandos de
   `psql`: cada archivo ya corre en su propia transacción, así que tampoco admite `CREATE INDEX CONCURRENTLY`).
2. Si crea una tabla que usa el servicio, agregar sus permisos en `permisos.sql`.
3. Nunca modificar una migración ya desplegada: los cambios siempre van en una nueva.

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

-- Actividad de un usuario en las últimas 24 h
SELECT fecha, host, metodo, ruta, status FROM nginx_acceso
WHERE usuario = '42' AND fecha > now() - interval '24 hours'
ORDER BY fecha DESC;

-- IPs con más 404
SELECT ip, count(*) FROM nginx_acceso
WHERE status = 404 AND fecha > now() - interval '24 hours'
GROUP BY ip ORDER BY 2 DESC LIMIT 20;

-- Últimos errores de nginx
SELECT fecha, nivel, server, mensaje, request FROM nginx_error ORDER BY fecha DESC LIMIT 50;
```
