@echo off
rem ============================================================================
rem  BAF: розвідка метаданих — ПАКЕТНИЙ ЗАПУСК НА ДЕМО-БАЗІ
rem
rem  Обробка тільки читає метадані. Нічого не змінює в базі та конфігурації.
rem  Режим ENTERPRISE, без монопольного режиму, без ключа /UC.
rem
rem  ПЕРЕД ЗАПУСКОМ перевірте три рядки нижче: шлях до платформи, шлях до
rem  демо-бази і шлях до .epf. Пароль лишайте порожнім, якщо в демо-базі
rem  його немає.
rem ============================================================================

set "CLIENT=C:\Program Files\BAF\8.3.19.1529\bin\1cv8.exe"
set "BASE=C:\bas\bases\sh_demo"
set "EPF=C:\bas\exchange\baf_discovery.epf"
set "USERNAME_1C=Админ"
set "PASSWORD_1C="

if not exist "%CLIENT%" (
  echo НЕ ЗНАЙДЕНО платформу: %CLIENT%
  exit /b 1
)
if not exist "%BASE%\1Cv8.1CD" (
  echo НЕ ЗНАЙДЕНО демо-базу: %BASE%
  exit /b 1
)
if not exist "%EPF%" (
  echo НЕ ЗНАЙДЕНО обробку: %EPF%
  exit /b 1
)

echo Запуск на ДЕМО-базі: %BASE%
echo.

"%CLIENT%" ENTERPRISE /F"%BASE%" /N"%USERNAME_1C%" /P"%PASSWORD_1C%" ^
  /Execute "%EPF%" /C"AUTO" /DisableStartupMessages

echo.
echo Код завершення 1С: %ERRORLEVEL%
echo Шукаємо результат:
dir /b "%TEMP%\baf_metadata.json" 2>nul || echo   файл не створено — запустіть обробку інтерактивно (див. README-discovery.md)
