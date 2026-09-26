#!/bin/bash
# Actualiza Lantano en producción: descarga el código, aplica las migraciones y reinicia el servicio.
# Se detiene en el primer error; si falla antes del reinicio, el servicio sigue corriendo con la versión anterior.
#
# Uso (como root), copiado en /root:
#   bash /root/actualizar_lantano.sh
set -euo pipefail
cd /opt/lantano

anterior=$(git rev-parse --short HEAD)

git pull --ff-only
actual=$(git rev-parse --short HEAD)
if [ "$anterior" = "$actual" ]; then
    echo "== Sin cambios."
    exit 0
fi
git log --oneline "$anterior..$actual"

venv/bin/python migrar.py
systemctl restart lantano
