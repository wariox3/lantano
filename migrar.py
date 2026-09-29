#!/usr/bin/env python3
"""
Aplica en orden las migraciones de migraciones/ que falten en la base de datos.

- Cada migración es un archivo NNNN_descripcion.sql con SQL plano; se ejecuta en
  su propia transacción y se registra en lantano_migracion.
- Solo avanzan: para deshacer un cambio se escribe una migración nueva.
- Un bloqueo de PostgreSQL evita que dos ejecuciones simultáneas (por ejemplo,
  desde dos servidores web) apliquen la misma migración.

Se conecta con los datos del .env, igual que el servicio; el usuario debe ser
dueño de la base.

Uso:
    python3 migrar.py
    python3 migrar.py --estado
"""
import argparse
import os
import re

import psycopg2

DIRECTORIO = os.path.dirname(os.path.abspath(__file__))
DIRECTORIO_MIGRACIONES = os.path.join(DIRECTORIO, 'migraciones')
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


def migrar(conexion):
    try:
        with conexion.cursor() as cursor:
            cursor.execute(SQL_BLOQUEO)
            cursor.execute(SQL_TABLA)
            registradas = aplicadas(cursor)
        conexion.commit()
    except psycopg2.errors.InsufficientPrivilege as e:
        raise SystemExit(f'{str(e).strip()}\nEl usuario del .env debe ser dueño de la base (ALTER DATABASE ... OWNER TO ...).')

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



def main():
    parser = argparse.ArgumentParser(description='Aplica las migraciones pendientes de la base de datos de Lantano.')
    parser.add_argument('--estado', action='store_true', help='muestra las migraciones aplicadas y pendientes')
    argumentos = parser.parse_args()

    # Importación diferida: leer_log importa ultima_version de este módulo
    from leer_log import crear_conexion
    try:
        conexion = crear_conexion()
    except psycopg2.OperationalError as e:
        raise SystemExit(f'No se pudo conectar: {str(e).strip()}')
    try:
        if argumentos.estado:
            mostrar_estado(conexion)
        else:
            migrar(conexion)
    finally:
        conexion.close()


if __name__ == '__main__':
    main()
