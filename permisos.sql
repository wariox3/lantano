-- Permisos mínimos del usuario del servicio. migrar.py los reaplica después de cada ejecución, así que una
-- migración que crea una tabla que usa el servicio solo tiene que agregarla aquí.
-- {usuario} lo reemplaza migrar.py por el nombre del usuario, ya escapado.

GRANT SELECT, INSERT ON nginx_acceso, nginx_error, nginx_linea_invalida TO {usuario};
GRANT SELECT, INSERT, UPDATE ON nginx_posicion TO {usuario};
GRANT USAGE ON SEQUENCE nginx_acceso_id_seq, nginx_error_id_seq, nginx_linea_invalida_id_seq TO {usuario};
GRANT SELECT ON lantano_migracion TO {usuario};
