#!/usr/bin/env python3
"""
Aplica en orden las migraciones de migraciones/ que falten en la base de datos y
reaplica los permisos del usuario del servicio (permisos.sql).

- Cada migración es un archivo NNNN_descripcion.sql con SQL plano; se ejecuta en
  su propia transacción y se registra en lantano_migracion.
- Solo avanzan: para deshacer un cambio se escribe una migración nueva.
- Un bloqueo de PostgreSQL evita que dos ejecuciones simultáneas (por ejemplo,
  desde dos servidores nginx) apliquen la misma migración.

Se ejecuta con un usuario administrador, no con el del servicio. Host, puerto,
base, SSL y usuario del servicio se toman del .env si no se indican.

Uso:
    python3 migrar.py --admin <usuario_admin>
    python3 migrar.py --admin <usuario_admin> --estado
"""
import argparse
import getpass
import os
import re
import sys

import psycopg2
from psycopg2 import sql
from decouple import config

DIRECTORIO = os.path.dirname(os.path.abspath(__file__))
DIRECTORIO_MIGRACIONES = os.path.join(DIRECTORIO, 'migraciones')
ARCHIVO_PERMISOS = os.path.join(DIRECTORIO, 'permisos.sql')
PATRON_MIGRACION = re.compile(r'^(\d{4})_[\w-]+\.sql$')

SQL_TABLA = '''
    CREATE TABLE IF NOT EXISTS lantano_migracion (
        version INTEGER PRIMARY KEY,
        nombre TEXT NOT NULL,
        aplicada TIMESTAMPTZ NOT NULL DEFAULT now()
    )
'''
SQL_BLOQUEO = "SELECT pg_advisory_lock(hashtext('lantano_migracion'))"


def listar_migraciones():
    """Devuelve [(version, nombre)] de migraciones/, ordenadas por versión."""
    migraciones = {}
    for nombre in sorted(os.listdir(DIRECTORIO_MIGRACIONES)):
        coincidencia = PATRON_MIGRACION.match(nombre)
        if not coincidencia:
            continue
        version = int(coincidencia.group(1))
        if version in migraciones:
            raise SystemExit(f'Versión repetida en migraciones/: {migraciones[version]} y {nombre}')
        migraciones[version] = nombre
    return sorted(migraciones.items())


def ultima_version():
    migraciones = listar_migraciones()
    return migraciones[-1][0] if migraciones else 0


def conectar(argumentos):
    parametros = dict(
        user=argumentos.admin,
        host=argumentos.host,
        port=argumentos.puerto,
        dbname=argumentos.base,
        sslmode=argumentos.sslmode,
        sslrootcert=argumentos.sslrootcert,
        connect_timeout=10,
        application_name='migrar',
    )
    try:
        # Sin clave: libpq usa PGPASSWORD o ~/.pgpass si existen
        return psycopg2.connect(**parametros)
    except psycopg2.OperationalError as e:
        if 'no password supplied' not in str(e) or not sys.stdin.isatty():
            raise
    return psycopg2.connect(password=getpass.getpass(f'Clave de {argumentos.admin}: '), **parametros)


def aplicadas(cursor):
    cursor.execute("SELECT to_regclass('lantano_migracion') IS NOT NULL")
    if not cursor.fetchone()[0]:
        return {}
    cursor.execute('SELECT version, aplicada FROM lantano_migracion')
    return dict(cursor.fetchall())


def mostrar_estado(conexion):
    with conexion, conexion.cursor() as cursor:
        registradas = aplicadas(cursor)
    for version, nombre in listar_migraciones():
        fecha = registradas.pop(version, None)
        print(f'{nombre:40} {fecha:%Y-%m-%d %H:%M:%S}' if fecha else f'{nombre:40} pendiente')
    for version in sorted(registradas):
        print(f'{version:04d} aplicada en la base pero no existe en migraciones/ (código anterior a la base)')


def migrar(conexion, usuario):
    with conexion.cursor() as cursor:
        cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (usuario,))
        if not cursor.fetchone():
            raise SystemExit(f'No existe el usuario {usuario} en PostgreSQL. Créelo antes (CREATE USER).')
        cursor.execute(SQL_BLOQUEO)
        cursor.execute(SQL_TABLA)
        registradas = aplicadas(cursor)
    conexion.commit()

    migraciones = listar_migraciones()
    desconocidas = set(registradas) - {version for version, _ in migraciones}
    if desconocidas:
        raise SystemExit(f'La base tiene migraciones que este código no conoce ({sorted(desconocidas)}). '
                         'Actualice el código antes de migrar.')

    pendientes = [(version, nombre) for version, nombre in migraciones if version not in registradas]
    for version, nombre in pendientes:
        with open(os.path.join(DIRECTORIO_MIGRACIONES, nombre), encoding='utf-8') as archivo:
            contenido = archivo.read()
        try:
            with conexion, conexion.cursor() as cursor:
                cursor.execute(contenido)
                cursor.execute('INSERT INTO lantano_migracion (version, nombre) VALUES (%s, %s)', (version, nombre))
        except psycopg2.Error as e:
            raise SystemExit(f'Error en {nombre}: {str(e).strip()}\nNo se aplicó; las anteriores quedaron aplicadas.')
        print(f'Aplicada {nombre}')
    if not pendientes:
        print(f'La base está al día (versión {ultima_version()}).')

    with open(ARCHIVO_PERMISOS, encoding='utf-8') as archivo:
        permisos = sql.SQL(archivo.read()).format(usuario=sql.Identifier(usuario))
    with conexion, conexion.cursor() as cursor:
        cursor.execute(permisos)
    print(f'Permisos aplicados a {usuario}.')


def main():
    parser = argparse.ArgumentParser(description='Aplica las migraciones pendientes de la base de datos de Lantano.')
    parser.add_argument('--admin', required=True, help='usuario de PostgreSQL con permisos para crear tablas')
    parser.add_argument('--host', default=config('NGINX_PG_DATABASE_HOST', default=None))
    parser.add_argument('--puerto', default=config('NGINX_PG_DATABASE_PORT', default='5432'))
    parser.add_argument('--base', default=config('NGINX_PG_DATABASE_NAME', default=None))
    parser.add_argument('--usuario', default=config('NGINX_PG_DATABASE_USER', default=None),
                        help='usuario del servicio que recibe los permisos')
    parser.add_argument('--sslmode', default=config('NGINX_PG_SSLMODE', default='prefer'))
    parser.add_argument('--sslrootcert', default=config('NGINX_PG_SSLROOTCERT', default='') or None)
    parser.add_argument('--estado', action='store_true', help='muestra las migraciones aplicadas y pendientes')
    argumentos = parser.parse_args()

    faltantes = [opcion for opcion in ('host', 'base', 'usuario') if not getattr(argumentos, opcion)]
    if faltantes:
        parser.error('faltan ' + ', '.join('--' + opcion for opcion in faltantes) + ' (no están en el .env)')

    try:
        conexion = conectar(argumentos)
    except psycopg2.OperationalError as e:
        raise SystemExit(f'No se pudo conectar: {str(e).strip()}')
    try:
        if argumentos.estado:
            mostrar_estado(conexion)
        else:
            migrar(conexion, argumentos.usuario)
    finally:
        conexion.close()


if __name__ == '__main__':
    main()
