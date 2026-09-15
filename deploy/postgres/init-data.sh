#!/bin/bash
# Створює окремого (не суперюзера) користувача БД для n8n.
# Виконується один раз — при першій ініціалізації тому postgres_data.
set -euo pipefail

if [ -z "${POSTGRES_NON_ROOT_USER:-}" ] || [ -z "${POSTGRES_NON_ROOT_PASSWORD:-}" ]; then
  echo "init-data.sh: POSTGRES_NON_ROOT_USER/PASSWORD не задані — n8n працюватиме від ${POSTGRES_USER}."
  exit 0
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set nonroot="$POSTGRES_NON_ROOT_USER" \
     --set nonrootpw="$POSTGRES_NON_ROOT_PASSWORD" \
     --set db="$POSTGRES_DB" <<-'EOSQL'
	CREATE USER :"nonroot" WITH PASSWORD :'nonrootpw';
	GRANT ALL PRIVILEGES ON DATABASE :"db" TO :"nonroot";
	GRANT ALL ON SCHEMA public TO :"nonroot";
	ALTER SCHEMA public OWNER TO :"nonroot";
EOSQL

echo "init-data.sh: користувача ${POSTGRES_NON_ROOT_USER} створено."
