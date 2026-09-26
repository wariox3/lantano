# Despliegue en producción

Guía para instalar, verificar, actualizar y revertir Lantano en producción. El funcionamiento interno y las
consultas de ejemplo están en el [README](README.md).

```
servidor nginx                                   servidor de base de datos
┌──────────────────────────────────────┐         ┌──────────────────────┐
│ /var/log/nginx/access.log (JSON)     │         │                      │
│ /var/log/nginx/error.log             │         │  PostgreSQL          │
│              │                       │  5432   │                      │
│              ▼                       │ ──────► │  nginx_acceso        │
│ lantano.service (usuario lognginx)   │         │  nginx_error         │
│ /opt/lantano/leer_log_nginx.py       │         │  nginx_posicion      │
│ /opt/lantano/migrar.py               │         │  nginx_linea_invalida│
└──────────────────────────────────────┘         │  lantano_migracion   │
                                                 └──────────────────────┘
```

## Requisitos

| Requisito | Dónde | Detalle |
| --- | --- | --- |
| Python 3 con `venv` | Servidor nginx | Probado con 3.12 (`sudo apt install python3-venv`) |
| git | Servidor nginx | Acceso a `https://github.com/wariox3/lantano.git` |
| `psql` (postgresql-client) | Servidor nginx | Para probar la conexión |
| nginx + logrotate | Servidor nginx | Con `delaycompress` (valor por defecto en Ubuntu) |
| PostgreSQL | Servidor de base de datos | Un usuario que pueda crear bases, usuarios y tablas |
| Red | Ambos | Puerto 5432 abierto solo desde la IP del servidor nginx |
| sudo | Ambos | Para crear usuarios, archivos en `/etc` y servicios |

El orden importa: **nginx → base de datos → servicio**. Si el servicio arranca antes de cambiar el formato de
nginx, las líneas de acceso que no son JSON terminan en `nginx_linea_invalida`.

## Paso 1: formato JSON en nginx

*Servidor nginx.*

Los sitios no declaran `access_log` ni `error_log`: todos heredan los de `/etc/nginx/nginx.conf` y escriben en
`/var/log/nginx/access.log` y `/var/log/nginx/error.log`. Se mantiene así y solo se cambia el formato del log de
acceso a JSON. Cada sitio se distingue por la columna `host` de `nginx_acceso` y `nginx_error`.

1. Confirmar que ningún sitio declara su propio `access_log` (solo deben aparecer las líneas de `nginx.conf`):

   ```bash
   sudo nginx -T 2>/dev/null | grep -nE '^# configuration file|access_log|error_log'
   ```

2. Respaldar `/etc/nginx/nginx.conf` y, dentro del bloque `http`, reemplazar la línea
   `access_log /var/log/nginx/access.log;` por el formato JSON y el log de acceso que lo usa:

   ```bash
   sudo cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.antes-lantano
   sudo nano /etc/nginx/nginx.conf
   ```

   ```nginx
   # Solo el prefijo de la API Key (erp_<hex>); el secreto después del punto nunca se escribe en el log
   map $http_x_api_key $api_key_prefijo {
       "~^(?<prefijo>erp_[0-9a-f]+)\."  $prefijo;
       default                          "";
   }

   log_format json_log escape=json '{"time":"$time_iso8601","host":"$host","ip":"$remote_addr",'
     '"method":"$request_method","uri":"$request_uri","protocol":"$server_protocol",'
     '"status":"$status","bytes":"$body_bytes_sent","referer":"$http_referer",'
     '"user_agent":"$http_user_agent","request_time":"$request_time",'
     '"upstream_time":"$upstream_response_time","upstream":"$upstream_addr",'
     '"usuario":"$upstream_http_x_usuario","api_key":"$api_key_prefijo"}';
   access_log /var/log/nginx/access.log json_log;
   ```

   - `log_format` debe quedar **antes** de `access_log`; si no, `nginx -t` falla con `unknown log format`.
   - La línea `access_log` original no debe quedar: nginx escribiría cada petición dos veces en el mismo
     archivo, una en cada formato.
   - `usuario` es la cabecera de respuesta `X-Usuario` que envía la aplicación con el usuario autenticado. Los
     servicios que no la envían dejan el campo vacío y se guarda `NULL`; no hace falta configurar nada en ellos.
     En los que sí la envían, agregar `proxy_hide_header X-Usuario;` en su `location` para que no llegue al
     navegador. Un cliente no puede falsificarla: es una cabecera de la respuesta del backend, no de la petición.
   - `api_key` es el prefijo de la cabecera `X-API-Key` de la petición (`erp_ab12cd34` de
     `erp_ab12cd34.<secreto>`). **Nunca registrar `$http_x_api_key` directamente**: escribiría la llave completa en
     el log. Es el prefijo que envió el cliente, no uno validado: con `status` 401 la llave era inválida o estaba
     expirada. Lo que no tiene el formato `erp_<hex>.` queda vacío.

3. Validar y recargar:

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

4. Hacer una petición a cada uno de los 3 sitios y comprobar que las líneas nuevas salen en JSON:

   ```bash
   sudo tail -n 3 /var/log/nginx/access.log
   ```

Mientras se use este esquema:

- Un sitio nuevo tampoco debe declarar `access_log`: si lo hace, deja de escribir en `access.log` con formato JSON.
- Si otra herramienta del servidor lee `access.log` en el formato por defecto (fail2ban, GoAccess, AWStats),
  deja de entenderlo.
- Al actualizar el paquete de nginx, `apt` puede preguntar si conservar el `nginx.conf` modificado: responder
  que se conserve (opción por defecto `N`).
- Para volver al formato por defecto: `sudo cp /etc/nginx/nginx.conf.antes-lantano /etc/nginx/nginx.conf`,
  `nginx -t` y `reload`.

## Paso 2: base de datos

*Servidor de base de datos.*

1. Crear el usuario del servicio y la base, con ese usuario como dueño (como administrador). Tiene todos los
   permisos sobre esa base y ninguno sobre las demás; las tablas las crea `migrar.py` en el paso 3.4:

   ```sql
   CREATE USER lognginx WITH PASSWORD '<clave>';
   CREATE DATABASE <base> OWNER lognginx;
   ```

2. Permitir la conexión en `pg_hba.conf` y recargar PostgreSQL (`sudo systemctl reload postgresql`):

   ```
   hostssl  <base>  lognginx  <ip_servidor_nginx>/32  scram-sha-256
   ```

   Si PostgreSQL no tiene SSL configurado, usar `host` en lugar de `hostssl` y `NGINX_PG_SSLMODE=prefer` en el
   `.env` del paso 3.3 (la conexión viaja sin cifrar).

3. Abrir el puerto 5432 en el firewall solo para la IP del servidor nginx. Con ufw:

   ```bash
   sudo ufw allow from <ip_servidor_nginx> to any port 5432 proto tcp
   ```

4. Desde el **servidor nginx**, probar la conexión:

   ```bash
   psql "host=<host> dbname=<base> user=lognginx" -c 'SELECT 1;'
   ```

## Paso 3: instalación del servicio

*Servidor nginx.*

1. Crear el usuario del sistema. Pertenece al grupo `adm` para poder leer `/var/log/nginx`:

   ```bash
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin --groups adm lognginx
   ```

2. Descargar el código e instalar dependencias:

   ```bash
   sudo git clone https://github.com/wariox3/lantano.git /opt/lantano
   sudo python3 -m venv /opt/lantano/venv
   sudo /opt/lantano/venv/bin/pip install -r /opt/lantano/requirements.txt
   ```

3. Configurar `.env` a partir de la plantilla y completar los datos de conexión:

   ```bash
   sudo cp /opt/lantano/.env.example /opt/lantano/.env
   sudo nano /opt/lantano/.env
   sudo chown root:lognginx /opt/lantano/.env && sudo chmod 640 /opt/lantano/.env
   ```

   | Variable | Obligatoria | Por defecto | Uso |
   | --- | --- | --- | --- |
   | `NGINX_PG_DATABASE_USER` | Sí | | Usuario de PostgreSQL |
   | `NGINX_PG_DATABASE_CLAVE` | Sí | | Clave |
   | `NGINX_PG_DATABASE_HOST` | Sí | | Host del servidor de base de datos |
   | `NGINX_PG_DATABASE_NAME` | Sí | | Nombre de la base |
   | `NGINX_PG_DATABASE_PORT` | No | `5432` | Puerto |
   | `NGINX_SERVIDOR` | No | vacío (`NULL`) | Nombre de este servidor nginx; se guarda en la columna `servidor` de `nginx_acceso` y `nginx_error`. **Obligatoria y distinta en cada servidor** si varios escriben en la misma base: también separa sus posiciones en `nginx_posicion`. No cambiarla después sin mover sus filas de `nginx_posicion`, o el servicio arranca desde el final de los archivos |
   | `NGINX_PG_SSLMODE` | No | `prefer` | `require` exige SSL; `verify-full` además valida certificado y nombre del host. La plantilla trae `require` |
   | `NGINX_PG_SSLROOTCERT` | No | | Ruta al certificado de la CA, necesario con `verify-ca` / `verify-full` (legible por `lognginx`) |
   | `NGINX_ACCESS_GLOB` | No | `/var/log/nginx/*access*.log` | Archivos de acceso vigilados |
   | `NGINX_ERROR_GLOB` | No | `/var/log/nginx/*error*.log` | Archivos de error vigilados |
   | `NGINX_LOTE` | No | `500` | Filas por inserción |
   | `NGINX_INTERVALO` | No | `5` | Segundos máximos entre guardados |
   | `NGINX_ESCANEO` | No | `1` | Segundos de espera cuando no hay líneas nuevas |
   | `NGINX_EXCLUIR` | No | vacío | Regex sobre la URI; lo que coincide no se guarda |
   | `NGINX_PARAMETROS_OCULTOS` | No | `token,password,key,secret` | Parámetros de URL cuyo valor se guarda como `***`. Basta con que el nombre contenga la palabra: `key` oculta `api_key`, `token` oculta `access_token` |

4. Crear las tablas. Usa la conexión del `.env` (usuario, clave, host, base y SSL):

   ```bash
   cd /opt/lantano && sudo venv/bin/python migrar.py
   ```

   Debe mostrar `Aplicada 0001_inicial.sql` y una línea por cada migración siguiente.

5. Instalar y arrancar el servicio:

   ```bash
   sudo cp /opt/lantano/lantano.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now lantano
   ```

El servicio empieza a leer desde el **final** de los archivos: los logs anteriores a la instalación no se cargan.

## Paso 4: verificación

1. Estado y log del servicio:

   ```bash
   systemctl status lantano
   journalctl -u lantano -n 50
   ```

   Debe aparecer `Conectado a PostgreSQL.` y una línea `Vigilando ...` por cada archivo de log.

2. Generar tráfico y confirmar que llega a la base (esperar unos 5 s):

   ```bash
   curl -s -o /dev/null https://<un_sitio>/
   ```

   ```sql
   SELECT servidor, archivo, posicion, actualizado FROM nginx_posicion ORDER BY actualizado DESC;
   SELECT fecha, host, ruta, parametros, status FROM nginx_acceso ORDER BY id DESC LIMIT 5;
   SELECT count(*) FROM nginx_linea_invalida WHERE creado > now() - interval '1 hour';
   ```

   Si `nginx_linea_invalida` se llena con líneas de acceso, `access.log` sigue recibiendo el formato por defecto:
   revisar que en `nginx.conf` no quedó la línea `access_log` original (paso 1.2).

3. Probar un reinicio: `sudo systemctl restart lantano` y comprobar en el log que dice `continúa en el byte ...`.

## Actualizar

```bash
cd /opt/lantano
sudo git log --oneline -1          # anotar el commit actual por si hay que revertir
sudo git pull
sudo venv/bin/pip install -r requirements.txt
sudo cp lantano.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo venv/bin/python migrar.py                    # aplica las migraciones pendientes; si no hay, no cambia nada
sudo systemctl restart lantano
journalctl -u lantano -f
```

`migrar.py` va **antes** del reinicio: al arrancar, el servicio compara la versión de la base
(`lantano_migracion`) con la última migración de su código y se detiene si no coinciden. Para ver qué está
aplicado: `sudo venv/bin/python migrar.py --estado`.

Con varios servidores nginx en la misma base, la migración se ejecuta una sola vez (desde el primero que se
actualiza; en los demás `migrar.py` no hace nada). Los servidores que aún tienen el código anterior:

- Si siguen corriendo y la migración cambió columnas que usan, reintentan sin avanzar ni perder líneas hasta que
  se actualicen.
- Si se reinician antes de actualizarse, se detienen con `más nueva que el código`.

Durante el reinicio no se pierden líneas: al arrancar continúa desde la posición guardada.

## Revertir

```bash
cd /opt/lantano
sudo git checkout <commit_anterior>
sudo venv/bin/pip install -r requirements.txt
sudo cp lantano.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl restart lantano
```

Para volver a la última versión: `sudo git checkout main && sudo git pull` y repetir los pasos.

Si entre ambos commits hay migraciones nuevas, el servicio no arranca (`más nueva que el código`): las migraciones
solo avanzan. En ese caso hay dos opciones:

- **Mejor:** corregir hacia adelante con una migración nueva en lugar de revertir el código.
- Deshacer a mano los cambios de esas migraciones y borrar sus filas: `DELETE FROM lantano_migracion WHERE
  version > <n>;`, donde `<n>` es la última migración del commit anterior (`ls migraciones/`).

## Desinstalar

```bash
sudo systemctl disable --now lantano
sudo rm /etc/systemd/system/lantano.service && sudo systemctl daemon-reload
sudo rm -rf /opt/lantano
sudo userdel lognginx
```

Las tablas y los datos quedan en la base de datos; borrarlos es una decisión aparte.

## Problemas frecuentes

| Síntoma en `journalctl -u lantano` | Causa | Solución |
| --- | --- | --- |
| `La base de datos no tiene las tablas. Ejecute migrar.py.` y el servicio se reinicia cada 10 s | Falta el paso 3.4 o se apunta a otra base | Ejecutar `migrar.py` (usa la base de `NGINX_PG_DATABASE_NAME`) |
| `La base de datos está en la versión N y el código espera la M. Ejecute migrar.py.` | Se actualizó el código sin migrar | Ejecutar `migrar.py` |
| `La base de datos está en la versión N, más nueva que el código (M). Actualice el código.` | Otro servidor ya migró la base, o se revirtió el código | Actualizar el código (ver [Actualizar](#actualizar)) o [Revertir](#revertir) |
| `Error de base de datos: column "..." of relation "..." does not exist. Reintento en N s.` | Otro servidor migró la base y este sigue corriendo con el código anterior | Actualizar el código de este servidor; mientras tanto no pierde líneas |
| `Error de base de datos: ... Reintento en N s.` | Sin red, `pg_hba.conf`, firewall o clave incorrecta | Probar con `psql` desde el servidor nginx (paso 2.4) |
| `server does not support SSL, but SSL was required` | `NGINX_PG_SSLMODE=require` y PostgreSQL sin SSL | Configurar SSL en PostgreSQL o usar `NGINX_PG_SSLMODE=prefer` |
| `root certificate file ... does not exist` o `certificate verify failed` | `verify-full` sin `NGINX_PG_SSLROOTCERT` válido o el host no coincide con el certificado | Revisar la ruta y permisos del certificado y que `NGINX_PG_DATABASE_HOST` sea el nombre del certificado |
| `migrar.py`: `permission denied for schema public` (o `permiso denegado al esquema public`) | El usuario del `.env` no es dueño de la base | Como administrador: `ALTER DATABASE <base> OWNER TO lognginx;` y repetir `migrar.py` |
| `No se puede abrir ...: Permission denied` | `lognginx` no está en el grupo `adm` | `sudo usermod -aG adm lognginx && sudo systemctl restart lantano` |
| `No hay archivos que coincidan con ...` | No existen `/var/log/nginx/access.log` ni `error.log` | Revisar el paso 1 y las rutas en `NGINX_ACCESS_GLOB` / `NGINX_ERROR_GLOB` |
| `fue rotado y no se encontró el archivo anterior; pueden faltar líneas` | El servicio estuvo detenido durante una rotación y el archivo ya se comprimió | Sin acción; vigilar que el servicio no quede detenido más de un día |
| `ModuleNotFoundError` o `UndefinedValueError` | Dependencias sin instalar o falta una variable obligatoria en `.env` | Repetir `pip install` o completar `.env` |

Para ver más detalle temporalmente, ejecutar a mano con el usuario del servicio:

```bash
sudo systemctl stop lantano
sudo -u lognginx /opt/lantano/venv/bin/python /opt/lantano/leer_log_nginx.py --debug
sudo systemctl start lantano
```

## Pendientes conocidos

- **Retención de datos:** no hay limpieza automática; `nginx_acceso` crece con cada petición. Definir cuántos
  días se guardan y programar el borrado.
