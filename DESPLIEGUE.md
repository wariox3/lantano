# Despliegue en producción

Guía para instalar, verificar, actualizar y revertir Lantano en producción. El funcionamiento interno y las
consultas de ejemplo están en el [README](README.md).

```
servidor nginx                                   servidor de base de datos
┌──────────────────────────────────────┐         ┌──────────────────────┐
│ /var/log/nginx/*_access.log (JSON)   │         │                      │
│ /var/log/nginx/*_error.log           │         │  PostgreSQL          │
│              │                       │  5432   │                      │
│              ▼                       │ ──────► │  nginx_acceso        │
│ lantano.service (usuario nginxlog)   │         │  nginx_error         │
│ /opt/lantano/leer_log_nginx.py       │         │  nginx_posicion      │
└──────────────────────────────────────┘         │  nginx_linea_invalida│
                                                 └──────────────────────┘
```

## Requisitos

| Requisito | Dónde | Detalle |
| --- | --- | --- |
| Python 3 con `venv` | Servidor nginx | Probado con 3.12 (`sudo apt install python3-venv`) |
| git | Servidor nginx | Acceso a `https://github.com/wariox3/lantano.git` |
| `psql` (postgresql-client) | Servidor nginx | Para probar la conexión y ejecutar `crear_tablas.sql` |
| nginx + logrotate | Servidor nginx | Con `delaycompress` (valor por defecto en Ubuntu) |
| PostgreSQL | Servidor de base de datos | Un usuario que pueda crear bases, usuarios y tablas |
| Red | Ambos | Puerto 5432 abierto solo desde la IP del servidor nginx |
| sudo | Ambos | Para crear usuarios, archivos en `/etc` y servicios |

El orden importa: **nginx → base de datos → servicio**. Si el servicio arranca antes de cambiar el formato de
nginx, las líneas de acceso que no son JSON terminan en `nginx_linea_invalida`.

## Paso 1: formato JSON en nginx

*Servidor nginx.*

1. Crear `/etc/nginx/conf.d/log_json.conf`:

   ```nginx
   log_format json_log escape=json '{"time":"$time_iso8601","host":"$host","ip":"$remote_addr",'
     '"method":"$request_method","uri":"$request_uri","protocol":"$server_protocol",'
     '"status":"$status","bytes":"$body_bytes_sent","referer":"$http_referer",'
     '"user_agent":"$http_user_agent","request_time":"$request_time",'
     '"upstream_time":"$upstream_response_time","upstream":"$upstream_addr"}';
   ```

2. En cada sitio de `/etc/nginx/sites-available/`, usar ese formato con nombres que contengan `access` y
   `error` (son los patrones que vigila el servicio):

   ```nginx
   access_log /var/log/nginx/proyecto_access.log json_log;
   error_log  /var/log/nginx/proyecto_error.log;
   ```

3. Validar y recargar:

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

4. Comprobar que las líneas nuevas salen en JSON:

   ```bash
   sudo tail -n 2 /var/log/nginx/proyecto_access.log
   ```

## Paso 2: base de datos

*Servidor de base de datos.*

1. Crear la base y el usuario del servicio (como administrador):

   ```sql
   CREATE DATABASE <base>;
   CREATE USER nginxlog WITH PASSWORD '<clave>';
   ```

2. Crear tablas, índices y permisos, conectado a esa base. `crear_tablas.sql` está en el repositorio; se puede
   ejecutar desde el servidor nginx después del paso 3.2. Es transaccional (todo o nada) y se puede ejecutar
   varias veces:

   ```bash
   psql -h <host> -U <admin> -d <base> -v usuario=nginxlog -f crear_tablas.sql
   ```

   El usuario del servicio queda con `SELECT, INSERT` sobre `nginx_acceso`, `nginx_error` y
   `nginx_linea_invalida`, y `SELECT, INSERT, UPDATE` sobre `nginx_posicion`. No puede borrar ni modificar logs.

3. Permitir la conexión en `pg_hba.conf` y recargar PostgreSQL (`sudo systemctl reload postgresql`):

   ```
   hostssl  <base>  nginxlog  <ip_servidor_nginx>/32  scram-sha-256
   ```

   Si PostgreSQL no tiene SSL configurado, usar `host` en lugar de `hostssl`.

4. Abrir el puerto 5432 en el firewall solo para la IP del servidor nginx. Con ufw:

   ```bash
   sudo ufw allow from <ip_servidor_nginx> to any port 5432 proto tcp
   ```

5. Desde el **servidor nginx**, probar la conexión:

   ```bash
   psql "host=<host> dbname=<base> user=nginxlog" -c 'SELECT count(*) FROM nginx_posicion;'
   ```

## Paso 3: instalación del servicio

*Servidor nginx.*

1. Crear el usuario del sistema. Pertenece al grupo `adm` para poder leer `/var/log/nginx`:

   ```bash
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin --groups adm nginxlog
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
   sudo chown root:nginxlog /opt/lantano/.env && sudo chmod 640 /opt/lantano/.env
   ```

   | Variable | Obligatoria | Por defecto | Uso |
   | --- | --- | --- | --- |
   | `NGINX_PG_DATABASE_USER` | Sí | | Usuario de PostgreSQL |
   | `NGINX_PG_DATABASE_CLAVE` | Sí | | Clave |
   | `NGINX_PG_DATABASE_HOST` | Sí | | Host del servidor de base de datos |
   | `NGINX_PG_DATABASE_NAME` | Sí | | Nombre de la base |
   | `NGINX_PG_DATABASE_PORT` | No | `5432` | Puerto |
   | `NGINX_ACCESS_GLOB` | No | `/var/log/nginx/*access*.log` | Archivos de acceso vigilados |
   | `NGINX_ERROR_GLOB` | No | `/var/log/nginx/*error*.log` | Archivos de error vigilados |
   | `NGINX_LOTE` | No | `500` | Filas por inserción |
   | `NGINX_INTERVALO` | No | `5` | Segundos máximos entre guardados |
   | `NGINX_ESCANEO` | No | `1` | Segundos de espera cuando no hay líneas nuevas |
   | `NGINX_EXCLUIR` | No | vacío | Regex sobre la URI; lo que coincide no se guarda |
   | `NGINX_PARAMETROS_OCULTOS` | No | `token,password,key,secret` | Parámetros de URL cuyo valor se guarda como `***` |

4. Instalar y arrancar el servicio:

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
   SELECT archivo, posicion, actualizado FROM nginx_posicion ORDER BY actualizado DESC;
   SELECT fecha, host, uri, status FROM nginx_acceso ORDER BY id DESC LIMIT 5;
   SELECT count(*) FROM nginx_linea_invalida WHERE creado > now() - interval '1 hour';
   ```

   Si `nginx_linea_invalida` se llena con líneas de acceso, algún sitio sigue sin `json_log` (paso 1).

3. Probar un reinicio: `sudo systemctl restart lantano` y comprobar en el log que dice `continúa en el byte ...`.

## Actualizar

```bash
cd /opt/lantano
sudo git log --oneline -1          # anotar el commit actual por si hay que revertir
sudo git pull
sudo /opt/lantano/venv/bin/pip install -r requirements.txt
sudo cp lantano.service /etc/systemd/system/ && sudo systemctl daemon-reload
psql -h <host> -U <admin> -d <base> -v usuario=nginxlog -f crear_tablas.sql   # solo si cambió
sudo systemctl restart lantano
journalctl -u lantano -f
```

Durante el reinicio no se pierden líneas: al arrancar continúa desde la posición guardada.

## Revertir

```bash
cd /opt/lantano
sudo git checkout <commit_anterior>
sudo /opt/lantano/venv/bin/pip install -r requirements.txt
sudo cp lantano.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl restart lantano
```

Para volver a la última versión: `sudo git checkout main && sudo git pull` y repetir los pasos. `crear_tablas.sql`
solo crea lo que no existe, así que revertir el código no requiere tocar la base de datos.

## Desinstalar

```bash
sudo systemctl disable --now lantano
sudo rm /etc/systemd/system/lantano.service && sudo systemctl daemon-reload
sudo rm -rf /opt/lantano
sudo userdel nginxlog
```

Las tablas y los datos quedan en la base de datos; borrarlos es una decisión aparte.

## Problemas frecuentes

| Síntoma en `journalctl -u lantano` | Causa | Solución |
| --- | --- | --- |
| `No existen las tablas. Ejecute crear_tablas.sql` y el servicio se reinicia cada 10 s | Falta el paso 2.2 o se apunta a otra base | Ejecutar `crear_tablas.sql` en la base de `NGINX_PG_DATABASE_NAME` |
| `Error de base de datos: ... Reintento en N s.` | Sin red, `pg_hba.conf`, firewall o clave incorrecta | Probar con `psql` desde el servidor nginx (paso 2.5) |
| `permission denied for table ...` | Permisos no otorgados al usuario del servicio | Ejecutar `crear_tablas.sql` con `-v usuario=nginxlog` |
| `No se puede abrir ...: Permission denied` | `nginxlog` no está en el grupo `adm` | `sudo usermod -aG adm nginxlog && sudo systemctl restart lantano` |
| `No hay archivos que coincidan con ...` | Los nombres de log no contienen `access`/`error` | Renombrar en nginx o ajustar `NGINX_ACCESS_GLOB` / `NGINX_ERROR_GLOB` |
| `fue rotado y no se encontró el archivo anterior; pueden faltar líneas` | El servicio estuvo detenido durante una rotación y el archivo ya se comprimió | Sin acción; vigilar que el servicio no quede detenido más de un día |
| `ModuleNotFoundError` o `UndefinedValueError` | Dependencias sin instalar o falta una variable obligatoria en `.env` | Repetir `pip install` o completar `.env` |

Para ver más detalle temporalmente, ejecutar a mano con el usuario del servicio:

```bash
sudo systemctl stop lantano
sudo -u nginxlog /opt/lantano/venv/bin/python /opt/lantano/leer_log_nginx.py --debug
sudo systemctl start lantano
```

## Pendientes conocidos

- **Retención de datos:** no hay limpieza automática; `nginx_acceso` crece con cada petición. Definir cuántos
  días se guardan y programar el borrado.
