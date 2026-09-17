#!/usr/bin/env bash
# Перевірка, чи вже увімкнений стандартний інтерфейс OData у публікації BAF.
# Перебирає типові імена публікації і показує HTTP-код по кожному.
#
#   ./check-odata.sh                                  # хост за замовчуванням, без авторизації
#   ./check-odata.sh https://host:46795               # свій хост
#   ./check-odata.sh https://host:46795 'логін:пароль' # з авторизацією
#
# Пароль у командному рядку потрапляє в history — після перевірки: history -c
# Без пароля тест теж корисний: 401 замість 404 означає, що endpoint існує.

set -u

HOST="${1:-https://vladiyan.extranet.club:46795}"
CREDS="${2:-}"
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

CANDIDATES=("" "/baf" "/BAF" "/unf" "/UNF" "/base" "/db" "/trade" "/vladiyan" "/1c" "/ua")

echo "Хост: $HOST"
echo "Авторизація: ${CREDS:+увімкнена}${CREDS:-немає}"
echo

for base in "${CANDIDATES[@]}"; do
  url="${HOST}${base}/odata/standard.odata/?\$format=json"
  code=$(curl -s -m 20 -o "$BODY" -w '%{http_code}' ${CREDS:+-u "$CREDS"} "$url" || echo 000)
  printf '%-12s HTTP %s' "${base:-/}" "$code"

  case "$code" in
    200)
      count=$(grep -o '"name"' "$BODY" | wc -l | tr -d ' ')
      if [ "$count" = "0" ]; then
        echo "  <- OData увімкнений, але СКЛАД ІНТЕРФЕЙСУ ПОРОЖНІЙ (крок 1.1 інструкції)"
      else
        echo "  <- OData ПРАЦЮЄ, опубліковано сутностей: $count"
        echo "               базовий URL: ${HOST}${base}/odata/standard.odata"
      fi
      head -c 300 "$BODY"; echo
      ;;
    401) echo "  <- endpoint існує, але не пройшла авторизація (потрібен користувач з 1С-аутентифікацією)" ;;
    403) echo "  <- увімкнений, але немає прав на об'єкти" ;;
    404) echo "  <- немає тут: або OData вимкнений, або інше ім'я публікації" ;;
    000) echo "  <- немає відповіді: спробуйте http:// замість https:// або перевірте фаєрвол" ;;
    *)   echo "" ;;
  esac
done

echo
echo "Якщо всюди 404 — скопіюйте повну адресу з адресного рядка веб-клієнта BAF"
echo "і запустіть скрипт із нею: ім'я публікації видно саме там."
