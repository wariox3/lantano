#!/usr/bin/env python3
"""
Lee de forma continua los logs de nginx o de Apache (access en formato JSON y
error) y los guarda en PostgreSQL. Pensado para correr como servicio de systemd.

- LANTANO_ORIGEN elige el servidor web (nginx o apache): las rutas por defecto
  de los logs y el formato del log de errores.
- Arranca desde el final de los archivos: no carga lo que ya existe.
- Guarda en posicion el inode y la posición leída de cada archivo, en la misma
  transacción que los registros, para continuar tras un reinicio sin duplicar
  ni perder líneas.
- Detecta la rotación de logrotate y termina de leer el archivo anterior.
- Si la base de datos no responde, deja de avanzar y reintenta.

Las tablas se crean antes con migrar.py. Al arrancar verifica que la base tenga
la última migración que trae el código.

Uso:
    python3 leer_log.py
"""
import argparse
import glob
import ipaddress
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values
from decouple import config, Csv

from migrar import ultima_version

ORIGENES = {'nginx': '/var/log/nginx', 'apache': '/var/log/apache2'}
ORIGEN = config('LANTANO_ORIGEN', default='nginx')
DIRECTORIO_LOGS = ORIGENES.get(ORIGEN, ORIGENES['nginx'])
ACCESS_GLOB = config('LANTANO_ACCESS_GLOB', default='') or f'{DIRECTORIO_LOGS}/*access*.log'
ERROR_GLOB = config('LANTANO_ERROR_GLOB', default='') or f'{DIRECTORIO_LOGS}/*error*.log'
LOTE = config('LANTANO_LOTE', default=500, cast=int)
INTERVALO = config('LANTANO_INTERVALO', default=5, cast=float)
ESCANEO = config('LANTANO_ESCANEO', default=1, cast=float)
EXCLUIR = config('LANTANO_EXCLUIR', default='')
PARAMETROS_OCULTOS = config('LANTANO_PARAMETROS_OCULTOS', default='token,password,key,secret', cast=Csv())
SERVIDOR = config('LANTANO_SERVIDOR', default='') or None
SERVIDOR_POSICION = SERVIDOR or ''  # clave en posicion, que no admite NULL
SSLMODE = config('LANTANO_PG_SSLMODE', default='prefer')
SSLROOTCERT = config('LANTANO_PG_SSLROOTCERT', default='') or None

DESCUBRIMIENTO = 30        # segundos entre búsquedas de archivos nuevos
GRACIA_ROTACION = 5        # segundos leyendo el archivo rotado antes de cambiar al nuevo
TAMANO_LECTURA = 64 * 1024
ESPERAS = (5, 10, 30, 60)  # reintentos de conexión
# Errores causados por los datos de una fila; cualquier otro (esquema, permisos) no debe desviar filas
ERRORES_DE_FILA = (psycopg2.DataError, psycopg2.IntegrityError)

PATRON_EXCLUIR = re.compile(EXCLUIR) if EXCLUIR else None
PARAMETROS_OCULTOS = [p for p in PARAMETROS_OCULTOS if p]
# Oculta los parámetros cuyo nombre contiene alguna de las palabras (api_key, access_token, ...)
PATRON_OCULTOS = re.compile(
    r'([?&][^=&#\s"]*(?:' + '|'.join(re.escape(p) for p in PARAMETROS_OCULTOS) + r')[^=&#\s"]*=)[^&#\s"]*',
    re.IGNORECASE,
) if PARAMETROS_OCULTOS else None

# nginx: 2026/09/29 10:00:00 [error] 1234#5678: *90 mensaje, client: 1.2.3.4, server: ...
PATRON_ERROR_NGINX = re.compile(
    r'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (\d+)#(\d+): (?:\*(\d+) )?(.*)$'
)
PATRON_CAMPOS_ERROR_NGINX = re.compile(
    r', (client|server|request|upstream|host|referrer): ("(?:[^"\\]|\\.)*"|[^,]*)'
)
# Apache 2.4 (ErrorLogFormat por defecto):
# [Tue Sep 29 10:00:00.123456 2026] [proxy:error] [pid 1234:tid 5678] (111)...: [client 1.2.3.4:5678] AH01114: mensaje, referer: ...
PATRON_ERROR_APACHE = re.compile(
    r'^\[([^\]]+)\] \[([^\]:]*):(\w+)\] \[pid (\d+)(?::tid (\d+))?\] (.*)$'
)
PATRON_CLIENTE_APACHE = re.compile(r'\[client ([^\]]+)\] ')
PATRON_REFERER_APACHE = re.compile(r', referer: (\S*)$')
FORMATOS_FECHA_APACHE = ('%a %b %d %H:%M:%S.%f %Y', '%a %b %d %H:%M:%S %Y')
# Apache escapa en el log de acceso los bytes no imprimibles o no ASCII como \xhh, que no es JSON válido.
# Se consume cada escape completo para no confundir \\x41 (barra invertida seguida de x41) con un byte.
PATRON_ESCAPES_APACHE = re.compile(r'(?:\\x[0-9a-fA-F]{2})+|\\v|\\.')
# Zona horaria sin dos puntos (-0500, %z de Apache), que datetime.fromisoformat no acepta antes de Python 3.11
PATRON_ZONA_SIN_DOS_PUNTOS = re.compile(r'([+-]\d{2})(\d{2})$')

SQL_ACCESO = '''
    INSERT INTO acceso (origen, fecha, archivo, host, ip, metodo, ruta, parametros, protocolo, status, bytes,
                        referer, user_agent, request_time, upstream_time, upstream, servidor, api_key, ip_real)
    VALUES %s
'''
SQL_ERROR = '''
    INSERT INTO error (origen, fecha, archivo, nivel, modulo, pid, tid, cid, mensaje, client, server,
                       request, upstream, host, referer, servidor)
    VALUES %s
'''
SQL_INVALIDA = 'INSERT INTO linea_invalida (archivo, linea, motivo) VALUES %s'
SQL_POSICION = '''
    INSERT INTO posicion (servidor, archivo, inode, posicion) VALUES %s
    ON CONFLICT (servidor, archivo) DO UPDATE
    SET inode = EXCLUDED.inode, posicion = EXCLUDED.posicion, actualizado = now()
'''


log = logging.getLogger('lantano')


def crear_conexion():
    return psycopg2.connect(
        user=config('LANTANO_PG_DATABASE_USER'),
        password=config('LANTANO_PG_DATABASE_CLAVE'),
        host=config('LANTANO_PG_DATABASE_HOST'),
        port=config('LANTANO_PG_DATABASE_PORT', default='5432'),
        dbname=config('LANTANO_PG_DATABASE_NAME'),
        sslmode=SSLMODE,
        sslrootcert=SSLROOTCERT,
        connect_timeout=10,
        application_name=f'lantano_{ORIGEN}',
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


# --- Conversión de campos ---------------------------------------------------

def texto(valor):
    if valor is None:
        return None
    valor = str(valor).replace('\x00', '')
    return None if valor in ('', '-') else valor


def entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def decimal(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def ip(valor):
    try:
        return str(ipaddress.ip_address(str(valor).strip()))
    except ValueError:
        return None


def ocultar(valor):
    if valor and PATRON_OCULTOS:
        return PATRON_OCULTOS.sub(r'\1***', valor)
    return valor


def prefijo_api_key(valor):
    """Solo el prefijo (erp_<hex>): si llegara la llave completa, descarta el secreto después del punto."""
    valor = texto(valor)
    return texto(valor.split('.', 1)[0]) if valor else None


def json_apache(linea):
    """Convierte los escapes de Apache que no son JSON: \\xhh (bytes, en UTF-8 si se puede) y \\v."""
    def convertir(coincidencia):
        escape = coincidencia.group(0)
        if escape == '\\v':
            return '\\u000b'
        if escape.startswith('\\x'):
            decodificado = bytes.fromhex(escape.replace('\\x', '')).decode('utf-8', errors='replace')
            # Los bytes de control quedan crudos; json.loads(strict=False) los acepta dentro de las cadenas
            return decodificado.replace('\\', '\\\\').replace('"', '\\"')
        return escape
    return PATRON_ESCAPES_APACHE.sub(convertir, linea)


def parsear_acceso(linea, archivo):
    """Devuelve la fila para acceso, None si la línea está excluida o lanza ValueError."""
    if ORIGEN == 'apache':
        datos = json.loads(json_apache(linea), strict=False)
    else:
        datos = json.loads(linea)
    if not isinstance(datos, dict):
        raise ValueError('la línea no es un objeto JSON')
    if not datos.get('time'):
        raise ValueError('falta el campo time')
    uri = datos.get('uri')
    if uri is None and datos.get('request'):
        # Apache: la URI sin decodificar solo está en la línea de petición (%r, "GET /ruta?query HTTP/1.1")
        partes = str(datos['request']).split(' ')
        uri = partes[1] if len(partes) >= 2 else None
    uri = ocultar(texto(uri))
    if PATRON_EXCLUIR and uri and PATRON_EXCLUIR.search(uri):
        return None
    ruta, _, parametros = (uri or '').partition('?')
    request_time = decimal(datos.get('request_time'))
    if ORIGEN == 'apache' and request_time is not None:
        request_time /= 1_000_000  # %D de Apache está en microsegundos
    return (
        ORIGEN,
        datetime.fromisoformat(PATRON_ZONA_SIN_DOS_PUNTOS.sub(r'\1:\2', str(datos['time']))),
        archivo,
        texto(datos.get('host')),
        ip(datos.get('ip')),
        texto(datos.get('method')),
        ruta or None,
        parametros or None,
        texto(datos.get('protocol')),
        entero(datos.get('status')),
        entero(datos.get('bytes')),
        ocultar(texto(datos.get('referer'))),
        texto(datos.get('user_agent')),
        request_time,
        texto(datos.get('upstream_time')),
        texto(datos.get('upstream')),
        SERVIDOR,
        prefijo_api_key(datos.get('api_key')),
        ip(datos.get('ip_real')),  # log_format anterior sin el campo: NULL
    )


def parsear_error_nginx(linea, archivo):
    """Devuelve la fila para error o lanza ValueError."""
    coincidencia = PATRON_ERROR_NGINX.match(linea)
    if not coincidencia:
        raise ValueError('formato de error de nginx no reconocido')
    fecha, nivel, pid, tid, cid, resto = coincidencia.groups()

    campos = {}
    inicio_campos = len(resto)
    for campo in PATRON_CAMPOS_ERROR_NGINX.finditer(resto):
        if campo.group(1) in campos:
            continue
        inicio_campos = min(inicio_campos, campo.start())
        valor = campo.group(2)
        if len(valor) >= 2 and valor.startswith('"') and valor.endswith('"'):
            valor = valor[1:-1]
        campos[campo.group(1)] = valor

    return (
        'nginx',
        datetime.strptime(fecha, '%Y/%m/%d %H:%M:%S').astimezone(),
        archivo,
        nivel,
        None,
        entero(pid),
        entero(tid),
        entero(cid),
        texto(resto[:inicio_campos]),
        ip(campos.get('client')),
        texto(campos.get('server')),
        ocultar(texto(campos.get('request'))),
        ocultar(texto(campos.get('upstream'))),
        texto(campos.get('host')),
        ocultar(texto(campos.get('referrer'))),
        SERVIDOR,
    )


def fecha_apache(valor):
    for formato in FORMATOS_FECHA_APACHE:
        try:
            return datetime.strptime(valor, formato).astimezone()
        except ValueError:
            continue
    raise ValueError(f'fecha de Apache no reconocida: {valor}')


def parsear_error_apache(linea, archivo):
    """Devuelve la fila para error o lanza ValueError."""
    coincidencia = PATRON_ERROR_APACHE.match(linea)
    if not coincidencia:
        raise ValueError('formato de error de Apache no reconocido')
    fecha, modulo, nivel, pid, tid, mensaje = coincidencia.groups()

    client = None
    cliente = PATRON_CLIENTE_APACHE.search(mensaje)
    if cliente:
        client = cliente.group(1).rsplit(':', 1)[0]  # ip:puerto, también en IPv6 (2001:db8::1:5678)
        mensaje = mensaje[:cliente.start()] + mensaje[cliente.end():]
    referer = None
    coincidencia_referer = PATRON_REFERER_APACHE.search(mensaje)
    if coincidencia_referer:
        referer = coincidencia_referer.group(1)
        mensaje = mensaje[:coincidencia_referer.start()]

    return (
        'apache',
        fecha_apache(fecha),
        archivo,
        nivel,
        texto(modulo),
        entero(pid),
        entero(tid),
        None,
        ocultar(texto(mensaje)),
        ip(client),
        None,
        None,
        None,
        None,
        ocultar(texto(referer)),
        SERVIDOR,
    )


def parsear_error_monolog(linea, archivo):
    """Línea JSON de Monolog (Symfony) que la aplicación escribe en stderr y termina en el log de errores.

    Devuelve la fila para error o lanza ValueError.
    """
    datos = json.loads(linea)
    if not isinstance(datos, dict) or not datos.get('datetime') or 'message' not in datos:
        raise ValueError('línea JSON que no es de Monolog')
    mensaje = str(datos['message'])
    contexto = datos.get('context')
    excepcion = contexto.get('exception') if isinstance(contexto, dict) else None
    if isinstance(excepcion, dict) and excepcion.get('file'):
        mensaje += f" [{excepcion.get('class', 'excepción')} en {excepcion['file']}]"
    return (
        ORIGEN,
        datetime.fromisoformat(PATRON_ZONA_SIN_DOS_PUNTOS.sub(r'\1:\2', str(datos['datetime']))),
        archivo,
        texto(str(datos.get('level_name') or '').lower()),
        f"symfony.{datos['channel']}" if datos.get('channel') else 'symfony',
        None,
        None,
        None,
        ocultar(texto(mensaje)),
        None,
        None,
        None,
        None,
        None,
        None,
        SERVIDOR,
    )


def parsear_error(linea, archivo):
    if linea.startswith('{'):
        return parsear_error_monolog(linea, archivo)
    if ORIGEN == 'apache':
        return parsear_error_apache(linea, archivo)
    return parsear_error_nginx(linea, archivo)


# --- Lectura continua ---------------------------------------------------------

class ArchivoLog:
    def __init__(self, ruta, tipo):
        self.ruta = ruta          # ruta vigilada, clave en posicion
        self.tipo = tipo          # 'acceso' o 'error'
        self.fh = None
        self.inode = None
        self.posicion = 0         # bytes de líneas completas ya procesadas
        self.resto = b''          # línea incompleta pendiente
        self.rotado_en = None     # momento en que se detectó la rotación
        self.en_eof = False

    def cerrar(self):
        if self.fh:
            self.fh.close()
            self.fh = None


class Lector:
    def __init__(self):
        self.conexion = None
        self.archivos = {}
        self.posiciones = {}
        self.pendientes = {'acceso': [], 'error': [], 'invalida': []}
        self.cambios = False
        self.detener = False

    def senal(self, signum, _frame):
        log.info('Señal %s recibida, deteniendo...', signum)
        self.detener = True

    def esperar(self, segundos):
        limite = time.monotonic() + segundos
        while not self.detener and time.monotonic() < limite:
            time.sleep(min(0.5, segundos))

    def total_pendientes(self):
        return sum(len(filas) for filas in self.pendientes.values())

    # --- Base de datos ---

    def asegurar_conexion(self):
        if self.conexion is None or self.conexion.closed:
            self.conexion = crear_conexion()
            log.info('Conectado a PostgreSQL.')

    def fallo_conexion(self, error, intento):
        espera = ESPERAS[min(intento, len(ESPERAS) - 1)]
        log.error('Error de base de datos: %s. Reintento en %s s.', str(error).strip(), espera)
        if self.conexion is not None:
            try:
                self.conexion.close()
            except psycopg2.Error:
                pass
            self.conexion = None
        self.esperar(espera)

    def cargar_posiciones(self):
        intento = 0
        while not self.detener:
            try:
                self.asegurar_conexion()
                with self.conexion, self.conexion.cursor() as cursor:
                    self.verificar_version(cursor)
                    cursor.execute(
                        'SELECT archivo, inode, posicion FROM posicion WHERE servidor = %s',
                        (SERVIDOR_POSICION,),
                    )
                    return {archivo: (int(inode), posicion) for archivo, inode, posicion in cursor.fetchall()}
            except psycopg2.errors.UndefinedTable:
                log.error('La base de datos no tiene las tablas. Ejecute migrar.py.')
                sys.exit(1)
            except psycopg2.Error as e:
                self.fallo_conexion(e, intento)
                intento += 1
        return None

    def verificar_version(self, cursor):
        cursor.execute('SELECT coalesce(max(version), 0) FROM lantano_migracion')
        base, codigo = cursor.fetchone()[0], ultima_version()
        if base < codigo:
            log.error('La base de datos está en la versión %s y el código espera la %s. Ejecute migrar.py.', base, codigo)
            sys.exit(1)
        if base > codigo:
            log.error('La base de datos está en la versión %s, más nueva que el código (%s). Actualice el código.',
                      base, codigo)
            sys.exit(1)

    def guardar_posiciones(self, cursor):
        filas = [(SERVIDOR_POSICION, a.ruta, a.inode, a.posicion) for a in self.archivos.values() if a.fh]
        if filas:
            execute_values(cursor, SQL_POSICION, filas)

    def insertar_lote(self):
        with self.conexion, self.conexion.cursor() as cursor:
            if self.pendientes['acceso']:
                execute_values(cursor, SQL_ACCESO, self.pendientes['acceso'], page_size=LOTE)
            if self.pendientes['error']:
                execute_values(cursor, SQL_ERROR, self.pendientes['error'], page_size=LOTE)
            if self.pendientes['invalida']:
                execute_values(cursor, SQL_INVALIDA, self.pendientes['invalida'], page_size=LOTE)
            self.guardar_posiciones(cursor)

    def insertar_individual(self):
        """Inserta fila por fila; las que fallan por sus datos van a linea_invalida.

        Otros errores se propagan y se deshace toda la transacción.
        """
        invalidas = list(self.pendientes['invalida'])
        with self.conexion, self.conexion.cursor() as cursor:
            for tabla, sql in (('acceso', SQL_ACCESO), ('error', SQL_ERROR)):
                for fila in self.pendientes[tabla]:
                    cursor.execute('SAVEPOINT fila')
                    try:
                        cursor.execute(sql, (fila,))
                        cursor.execute('RELEASE SAVEPOINT fila')
                    except ERRORES_DE_FILA as e:
                        cursor.execute('ROLLBACK TO SAVEPOINT fila')
                        invalidas.append((fila[2], repr(fila), f'{type(e).__name__}: {str(e).strip()}'))
            if invalidas:
                execute_values(cursor, SQL_INVALIDA, invalidas)
            self.guardar_posiciones(cursor)

    def guardar(self):
        """Guarda pendientes y posiciones. Reintenta hasta lograrlo o hasta recibir una señal."""
        intento = 0
        individual = False
        while True:
            try:
                self.asegurar_conexion()
                if individual:
                    self.insertar_individual()
                else:
                    self.insertar_lote()
                break
            except ERRORES_DE_FILA as e:
                if not individual:
                    log.warning('Error al insertar el lote (%s). Se insertan las filas una por una.', str(e).strip())
                    individual = True
                    continue
                error = e
            except psycopg2.Error as e:
                # Conexión, esquema o permisos: se reintenta sin avanzar la posición
                error = e
            if self.detener:
                log.warning('No se pudo guardar al detener; se retomará desde la última posición guardada.')
                return False
            self.fallo_conexion(error, intento)
            intento += 1

        for archivo in self.archivos.values():
            if archivo.fh:
                self.posiciones[archivo.ruta] = (archivo.inode, archivo.posicion)
        total = self.total_pendientes()
        if total:
            log.debug('Guardadas %s filas.', total)
        for filas in self.pendientes.values():
            filas.clear()
        self.cambios = False
        return True

    # --- Archivos ---

    def abrir(self, archivo, ruta_real, posicion):
        fh = open(ruta_real, 'rb')
        estado = os.fstat(fh.fileno())
        if posicion > estado.st_size:
            log.warning('%s es más pequeño que la posición guardada; se lee desde el inicio.', ruta_real)
            posicion = 0
        fh.seek(posicion)
        archivo.cerrar()
        archivo.fh = fh
        archivo.inode = estado.st_ino
        archivo.posicion = posicion
        archivo.resto = b''
        archivo.rotado_en = None
        archivo.en_eof = False
        self.cambios = True

    def abrir_inicial(self, archivo, inicial):
        guardada = self.posiciones.get(archivo.ruta)
        estado = os.stat(archivo.ruta)
        if guardada is None:
            posicion = estado.st_size if inicial else 0
            self.abrir(archivo, archivo.ruta, posicion)
            log.info('Vigilando %s (%s) desde el byte %s.', archivo.ruta, archivo.tipo, posicion)
        elif guardada[0] == estado.st_ino:
            self.abrir(archivo, archivo.ruta, guardada[1])
            log.info('Vigilando %s (%s), continúa en el byte %s.', archivo.ruta, archivo.tipo, archivo.posicion)
        else:
            rotado = buscar_rotado(archivo.ruta, guardada[0])
            if rotado:
                self.abrir(archivo, rotado, guardada[1])
                log.info('%s fue rotado; se termina de leer %s desde el byte %s.', archivo.ruta, rotado, archivo.posicion)
            else:
                self.abrir(archivo, archivo.ruta, 0)
                log.warning('%s fue rotado y no se encontró el archivo anterior; pueden faltar líneas.', archivo.ruta)

    def descubrir(self, inicial):
        for tipo, patron in (('acceso', ACCESS_GLOB), ('error', ERROR_GLOB)):
            for ruta in sorted(glob.glob(patron)):
                if ruta in self.archivos or not os.path.isfile(ruta):
                    continue
                archivo = ArchivoLog(ruta, tipo)
                try:
                    self.abrir_inicial(archivo, inicial)
                except OSError as e:
                    log.error('No se puede abrir %s: %s', ruta, e)
                    continue
                self.archivos[ruta] = archivo
        if inicial and not self.archivos:
            log.warning('No hay archivos que coincidan con %s ni %s.', ACCESS_GLOB, ERROR_GLOB)

    def procesar_linea(self, archivo, linea):
        contenido = linea.decode('utf-8', errors='replace').replace('\x00', '').strip()
        if not contenido:
            return
        try:
            if archivo.tipo == 'acceso':
                fila = parsear_acceso(contenido, archivo.ruta)
                if fila:
                    self.pendientes['acceso'].append(fila)
            else:
                self.pendientes['error'].append(parsear_error(contenido, archivo.ruta))
        except (ValueError, TypeError) as e:
            self.pendientes['invalida'].append((archivo.ruta, ocultar(contenido), f'{type(e).__name__}: {e}'))

    def leer(self, archivo):
        """Lee las líneas nuevas. Devuelve True si leyó algo."""
        leido = False
        archivo.en_eof = False
        while True:
            datos = archivo.fh.read(TAMANO_LECTURA)
            if not datos:
                archivo.en_eof = True
                break
            leido = True
            archivo.resto += datos
            *completas, archivo.resto = archivo.resto.split(b'\n')
            for linea in completas:
                archivo.posicion += len(linea) + 1
                self.procesar_linea(archivo, linea)
            if completas:
                self.cambios = True
            if self.total_pendientes() >= LOTE:
                break
        return leido

    def rotacion_lista(self, archivo):
        """Revisa rotación o truncado. Devuelve True cuando hay que cambiar al archivo nuevo."""
        try:
            estado = os.stat(archivo.ruta)
        except OSError:
            estado = None

        if estado is not None and estado.st_ino == archivo.inode:
            archivo.rotado_en = None
            if estado.st_size < archivo.fh.tell():
                log.warning('%s fue truncado; se lee desde el inicio.', archivo.ruta)
                archivo.fh.seek(0)
                archivo.posicion = 0
                archivo.resto = b''
                self.cambios = True
            return False

        if estado is None:
            return False
        if archivo.rotado_en is None:
            archivo.rotado_en = time.monotonic()
            log.info('Rotación detectada en %s.', archivo.ruta)
            return False
        return archivo.en_eof and time.monotonic() - archivo.rotado_en >= GRACIA_ROTACION

    def completar_rotacion(self, archivo):
        if archivo.resto:
            archivo.posicion += len(archivo.resto)
            self.procesar_linea(archivo, archivo.resto)
            archivo.resto = b''
        if not self.guardar():
            return
        try:
            self.abrir(archivo, archivo.ruta, 0)
            log.info('Vigilando el nuevo %s.', archivo.ruta)
        except OSError as e:
            log.error('No se puede abrir el nuevo %s: %s', archivo.ruta, e)
            archivo.cerrar()
            del self.archivos[archivo.ruta]

    # --- Ciclo principal ---

    def ejecutar(self):
        signal.signal(signal.SIGTERM, self.senal)
        signal.signal(signal.SIGINT, self.senal)

        posiciones = self.cargar_posiciones()
        if posiciones is None:
            return
        self.posiciones = posiciones
        self.descubrir(inicial=True)

        ultimo_guardado = ultimo_descubrimiento = time.monotonic()
        try:
            while not self.detener:
                leido = False
                for archivo in list(self.archivos.values()):
                    leido = self.leer(archivo) or leido
                    if self.total_pendientes() >= LOTE:
                        self.guardar()
                        ultimo_guardado = time.monotonic()
                    if self.rotacion_lista(archivo):
                        self.completar_rotacion(archivo)
                        ultimo_guardado = time.monotonic()
                    if self.detener:
                        break

                ahora = time.monotonic()
                if (self.cambios or self.total_pendientes()) and ahora - ultimo_guardado >= INTERVALO:
                    self.guardar()
                    ultimo_guardado = time.monotonic()
                if ahora - ultimo_descubrimiento >= DESCUBRIMIENTO:
                    self.descubrir(inicial=False)
                    ultimo_descubrimiento = ahora
                if not leido:
                    self.esperar(ESCANEO)

            if self.cambios or self.total_pendientes():
                self.guardar()
        finally:
            for archivo in self.archivos.values():
                archivo.cerrar()
            if self.conexion is not None and not self.conexion.closed:
                self.conexion.close()
            log.info('Detenido.')


def buscar_rotado(ruta, inode):
    """Busca el archivo rotado (sin comprimir) que conserva el inode guardado."""
    for candidato in (ruta + '.1', ruta + '-' + datetime.now().strftime('%Y%m%d')):
        try:
            if os.stat(candidato).st_ino == inode:
                return candidato
        except OSError:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description='Lee los logs de nginx o Apache y los guarda en PostgreSQL.')
    parser.add_argument('--debug', action='store_true', help='muestra mensajes de depuración')
    argumentos = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if argumentos.debug else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    if ORIGEN not in ORIGENES:
        log.error('LANTANO_ORIGEN debe ser %s, no %r.', ' o '.join(ORIGENES), ORIGEN)
        sys.exit(1)
    log.info('Origen: %s.', ORIGEN)
    Lector().ejecutar()


if __name__ == '__main__':
    main()
