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

# Кандидати-пакети для soname. Порівнюємо в нижньому регістрі: реальні імена
# бувають з великими літерами (libXdamage.so.1), а імена пакетів — ні.
pkg_candidates() {
    local soname lower base ver
    soname="$1"
    lower="$(printf '%s' "$soname" | tr 'A-Z' 'a-z')"

    case "$lower" in
        libpango-1.0.so.0|libpangoft2-1.0.so.0) echo libpango-1.0-0; return ;;
        libpangocairo-1.0.so.0) echo libpangocairo-1.0-0; return ;;
        libcairo.so.2) echo libcairo2; return ;;
        libcairo-gobject.so.2) echo libcairo-gobject2; return ;;
        libnss3.so|libsmime3.so|libnssutil3.so|libssl3.so) echo libnss3; return ;;
        libnspr4.so|libplc4.so|libplds4.so) echo libnspr4; return ;;
        libatk-1.0.so.0) echo libatk1.0-0; return ;;
        libatk-bridge-2.0.so.0) echo libatk-bridge2.0-0; return ;;
        libatspi.so.0) echo libatspi2.0-0; return ;;
        libcups.so.2) echo libcups2; return ;;
        libdbus-1.so.3) echo libdbus-1-3; return ;;
        libgbm.so.1) echo libgbm1; return ;;
        libglib-2.0.so.0|libgobject-2.0.so.0|libgio-2.0.so.0|libgmodule-2.0.so.0) echo libglib2.0-0; return ;;
        libgtk-3.so.0) echo libgtk-3-0; return ;;
        libgdk-3.so.0) echo libgtk-3-0; return ;;
        libgdk_pixbuf-2.0.so.0) echo libgdk-pixbuf-2.0-0; return ;;
        libharfbuzz.so.0) echo libharfbuzz0b; return ;;
        libjpeg.so.62) echo libjpeg62-turbo; return ;;
        libbrotlidec.so.1|libbrotlicommon.so.1) echo libbrotli1; return ;;
        libpcre2-8.so.0) echo libpcre2-8-0; return ;;
        libpixman-1.so.0) echo libpixman-1-0; return ;;
        libpng16.so.16) echo libpng16-16; return ;;
        libgraphite2.so.3) echo libgraphite2-3; return ;;
        libepoxy.so.0) echo libepoxy0; return ;;
        libxcb-*.so.0) echo "lib${lower#lib}" | sed 's/\.so\.0$/-0/'; return ;;
        libx11-xcb.so.1) echo libx11-xcb1; return ;;
        libwayland-*.so.0) echo "${lower%.so.0}0"; return ;;
    esac

    # Загальне правило: libXdamage.so.1 -> libxdamage1, libfoo.so.3 -> libfoo3 / libfoo-3.
    base="${lower%%.so*}"
    ver="${soname##*.so}"
    ver="${ver#.}"
    if [ -n "$ver" ]; then
        echo "$base$ver" "$base-$ver" "$base"
    else
        echo "$base"
    fi
}

# Перша назва, яка існує в індексі. На Debian 13 / Ubuntu 24.04 справжній пакет
# часто має суфікс t64, а без нього лишається порожній transitional — тому t64 першим.
pkg_for_soname() {
    local candidate name found
    for candidate in $(pkg_candidates "$1"); do
        for name in "${candidate}t64" "$candidate"; do
            # Саме grep -c, а не grep -q: -q виходить на першому збігу, apt-cache
            # отримує SIGPIPE, і pipefail видає це за «пакета немає».
            found="$(apt_cache show "$name" 2>/dev/null | grep -c '^Filename:' || true)"
            if [ "${found:-0}" -gt 0 ]; then
                echo "$name"
                return 0
            fi
        done
    done
    return 1
}

browser_binary() {
    local bin
    bin="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f -name 'headless_shell' 2>/dev/null | head -1 || true)"
    [ -n "$bin" ] || bin="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f -name 'chrome' 2>/dev/null | head -1 || true)"
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
TRIED=""

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
        # Пакет для цієї бібліотеки вже качали, а її досі немає — вгадали не той.
        # Далі не перебираємо, інакше кола крутитимуться даремно.
        case " $TRIED " in *" $soname "*) UNKNOWN="$UNKNOWN $soname"; continue ;; esac
        if pkg="$(pkg_for_soname "$soname" 2>/dev/null)"; then
            WANTED="$WANTED $pkg"
            TRIED="$TRIED $soname"
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
        # Пакети, які ми самі шукали через відсутню бібліотеку, качаємо завжди:
        # dpkg може вважати їх встановленими, хоч це порожній transitional-пакет,
        # а справжні файли лежать у версії з суфіксом t64.
        case " $WANTED " in *" $pkg "*) TO_GET="$TO_GET $pkg"; continue ;; esac
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
