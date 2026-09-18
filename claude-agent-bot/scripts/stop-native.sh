#!/usr/bin/env bash
# Зупинка бота за pid-файлом. Навмисно без pkill по шаблону: на спільному
# сервері "app.main" збігається і з чужими процесами.
set -euo pipefail

cd "$(dirname "$0")/.."
PIDFILE="data/bot.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "Бот не запущений (немає $PIDFILE)"
    exit 0
fi

PID="$(cat "$PIDFILE")"
case "$PID" in
    ''|*[!0-9]*) echo "Зіпсований $PIDFILE: $PID" >&2; rm -f "$PIDFILE"; exit 1 ;;
esac

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Процес $PID уже не живий — прибираю $PIDFILE"
    rm -f "$PIDFILE"
    exit 0
fi

# Переконуємось, що це справді наш бот, а не чужий процес, якому дістався той самий pid.
CMD="$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null || true)"
case "$CMD" in
    *app.main*) ;;
    *) echo "PID $PID — це не бот ($CMD). Нічого не чіпаю." >&2; exit 1 ;;
esac

echo "Зупиняю бота (pid $PID)"
kill "$PID"
for _ in $(seq 1 20); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
done
if kill -0 "$PID" 2>/dev/null; then
    echo "Не зупинився за 10 с — надсилаю KILL"
    kill -9 "$PID" 2>/dev/null || true
fi
rm -f "$PIDFILE"
echo "Зупинено"
