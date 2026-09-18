#!/usr/bin/env bash
# Системні бібліотеки chromium без root: розпаковуємо .deb у vendor/syslibs
# і підсовуємо їх браузеру через LD_LIBRARY_PATH.
#
# Нічого не встановлюється в систему: apt-get download і dpkg -x працюють
# від звичайного користувача, а пакети лягають у папку проєкту.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LIBDIR="$ROOT/vendor/syslibs"
DEBDIR="$ROOT/vendor/deb-cache"
APTDIR="$ROOT/vendor/apt"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!  %s\033[0m\n' "$1"; }
die()  { printf '\033[31mПомилка: %s\033[0m\n' "$1" >&2; exit 1; }

command -v apt-get >/dev/null || die "потрібен apt-get (Debian/Ubuntu)"
command -v dpkg >/dev/null || die "потрібен dpkg"

# Системний індекс apt без root не оновити, а якщо він застарілий — завантаження
# дає 404 на версію, якої в дзеркалі вже немає. Тому тримаємо свій індекс у vendor/.
mkdir -p "$APTDIR/lists/partial" "$APTDIR/cache/archives/partial"
: > "$APTDIR/status"
APT_OPTS=(
    -o "Dir::State::Lists=$APTDIR/lists"
    -o "Dir::Cache=$APTDIR/cache"
    -o "Dir::State::status=$APTDIR/status"
    -o Debug::NoLocking=1
    -o APT::Sandbox::User=root
)
apt_get()   { apt-get "${APT_OPTS[@]}" "$@"; }
apt_cache() { apt-cache "${APT_OPTS[@]}" "$@"; }

# Ніколи не тягнемо сюди glibc і компанію: підміна завантажувача ламає все,
# що запускається, а ці бібліотеки в системі вже є.
NEVER="libc6 libc-bin libgcc-s1 libstdc++6 zlib1g"

# Яка бібліотека в якому пакеті. На нових Debian/Ubuntu частина пакетів
# має суфікс t64 — перевіряємо обидві назви.
pkg_for_soname() {
    local base
    case "$1" in
        libpango-1.0.so.0|libpangoft2-1.0.so.0) base=libpango-1.0-0 ;;
        libpangocairo-1.0.so.0) base=libpangocairo-1.0-0 ;;
        libcairo.so.2) base=libcairo2 ;;
        libnss3.so|libsmime3.so|libnssutil3.so|libssl3.so) base=libnss3 ;;
        libnspr4.so|libplc4.so|libplds4.so) base=libnspr4 ;;
        libxkbcommon.so.0) base=libxkbcommon0 ;;
        libatk-1.0.so.0) base=libatk1.0-0 ;;
        libatk-bridge-2.0.so.0) base=libatk-bridge2.0-0 ;;
        libatspi.so.0) base=libatspi2.0-0 ;;
        libcups.so.2) base=libcups2 ;;
        libdbus-1.so.3) base=libdbus-1-3 ;;
        libdrm.so.2) base=libdrm2 ;;
        libgbm.so.1) base=libgbm1 ;;
        libexpat.so.1) base=libexpat1 ;;
        libxcomposite.so.1) base=libxcomposite1 ;;
        libxdamage.so.1) base=libxdamage1 ;;
        libxfixes.so.3) base=libxfixes3 ;;
        libxrandr.so.2) base=libxrandr2 ;;
        libxrender.so.1) base=libxrender1 ;;
        libxtst.so.6) base=libxtst6 ;;
        libxshmfence.so.1) base=libxshmfence1 ;;
        libasound.so.2) base=libasound2 ;;
        libglib-2.0.so.0|libgobject-2.0.so.0|libgio-2.0.so.0) base=libglib2.0-0 ;;
        libgtk-3.so.0) base=libgtk-3-0 ;;
        libgdk_pixbuf-2.0.so.0) base=libgdk-pixbuf-2.0-0 ;;
        libudev.so.1) base=libudev1 ;;
        libwayland-client.so.0) base=libwayland-client0 ;;
        libharfbuzz.so.0) base=libharfbuzz0b ;;
        libfontconfig.so.1) base=libfontconfig1 ;;
        libfreetype.so.6) base=libfreetype6 ;;
        *) return 1 ;;
    esac
    if apt_cache show "$base" >/dev/null 2>&1; then
        echo "$base"
    elif apt_cache show "${base}t64" >/dev/null 2>&1; then
        echo "${base}t64"
    else
        return 1
    fi
}

browser_binary() {
    local bin
    bin="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f -name 'headless_shell' 2>/dev/null | head -1)"
    [ -n "$bin" ] || bin="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f -name 'chrome' 2>/dev/null | head -1)"
    echo "$bin"
}

ld_path() {
    [ -d "$LIBDIR" ] || return 0
    find "$LIBDIR" -name '*.so*' -type f -printf '%h\n' 2>/dev/null | sort -u | paste -sd: -
}

missing_sonames() {
    LD_LIBRARY_PATH="$(ld_path)" ldd "$1" 2>/dev/null | awk '/not found/{print $1}' | sort -u
}

BIN="$(browser_binary)"
[ -n "$BIN" ] || die "не знайшов chromium у $PLAYWRIGHT_BROWSERS_PATH — спершу ./scripts/setup-native.sh"

say "Чого бракує"
MISSING="$(missing_sonames "$BIN")"
if [ -z "$MISSING" ]; then
    echo "нічого: chromium уже має всі бібліотеки"
else
    printf '   %s\n' $MISSING
fi

mkdir -p "$LIBDIR" "$DEBDIR"

if [ -n "$MISSING" ]; then
    say "Оновлюю власний індекс apt (у $APTDIR)"
    apt_get update >/dev/null 2>&1 || warn "індекс оновити не вдалося — пробую з тим, що є"
fi

ROUND=0

# Розпакований пакет може привести за собою нові залежності, тому крутимо
# кілька кіл: поставили — перевірили ldd ще раз — поставили те, що вилізло.
while [ -n "$MISSING" ] && [ "$ROUND" -lt 5 ]; do
    ROUND=$((ROUND + 1))
    say "Коло $ROUND: шукаю пакети"

    WANTED=""
    UNKNOWN=""
    for soname in $MISSING; do
        if pkg="$(pkg_for_soname "$soname" 2>/dev/null)"; then
            WANTED="$WANTED $pkg"
        else
            UNKNOWN="$UNKNOWN $soname"
        fi
    done

    [ -z "$UNKNOWN" ] || warn "не знаю, у якому пакеті шукати:$UNKNOWN"
    if [ -z "$WANTED" ]; then
        break
    fi
    echo "  пакети:$WANTED"

    # Разом із залежностями, але без того, що в системі вже стоїть.
    ALL="$(apt_cache depends --recurse --no-recommends --no-suggests --no-conflicts \
            --no-breaks --no-replaces --no-enhances $WANTED 2>/dev/null \
            | grep -E '^[a-zA-Z0-9]' | sed 's/:.*//' | sort -u || true)"

    TO_GET=""
    for pkg in $ALL; do
        case " $NEVER " in *" $pkg "*) continue ;; esac
        # FORCE_DOWNLOAD=1 — качати й те, що в системі вже є (стара версія бібліотеки)
        if [ "${FORCE_DOWNLOAD:-0}" != "1" ]; then
            dpkg -s "$pkg" >/dev/null 2>&1 && continue   # уже є в системі
        fi
        TO_GET="$TO_GET $pkg"
    done

    if [ -z "$TO_GET" ]; then
        warn "нових пакетів для завантаження немає"
        break
    fi

    say "Коло $ROUND: качаю"
    GOT=0
    for pkg in $TO_GET; do
        if ( cd "$DEBDIR" && apt_get download "$pkg" >/dev/null 2>&1 ); then
            GOT=$((GOT + 1))
        else
            warn "не завантажився: $pkg"
        fi
    done
    if [ "$GOT" = "0" ]; then
        warn "жоден пакет не завантажився — далі крутити нема сенсу"
        break
    fi

    say "Коло $ROUND: розпаковую в $LIBDIR"
    COUNT=0
    for deb in "$DEBDIR"/*.deb; do
        [ -e "$deb" ] || continue
        dpkg -x "$deb" "$LIBDIR"
        COUNT=$((COUNT + 1))
    done
    echo "  розпаковано пакетів: $COUNT"
    rm -f "$DEBDIR"/*.deb

    MISSING="$(missing_sonames "$BIN")"
done

LD="$(ld_path)"

say "Підсумок"
if [ -n "$MISSING" ]; then
    warn "усе ще бракує:"
    printf '   %s\n' $MISSING
    warn "браузер лишається вимкненим"
    exit 1
fi

echo "усі бібліотеки на місці"
echo "LD_LIBRARY_PATH: $LD"

# Перевірка ділом: запускаємо chromium і питаємо версію.
if VERSION="$(LD_LIBRARY_PATH="$LD" "$BIN" --version 2>&1)"; then
    echo "chromium запускається: $VERSION"
else
    warn "бібліотеки знайшлись, але chromium не стартував:"
    printf '   %s\n' "$VERSION"
    exit 1
fi

say ".env"
LD="$LD" python3 - <<'PY'
import os
from pathlib import Path

updates = {"BROWSER_ENABLED": "1", "BROWSER_LD_LIBRARY_PATH": os.environ["LD"]}
path = Path(".env")
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("BROWSER_ENABLED=1 і BROWSER_LD_LIBRARY_PATH проставлено")
PY

cat <<EOF

Готово. Перезапусти бота:
  pkill -f 'app.main' ; nohup ./scripts/run-native.sh > data/bot.log 2>&1 &
EOF
