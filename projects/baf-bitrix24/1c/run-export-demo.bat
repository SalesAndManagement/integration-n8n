@echo off
rem BAF: проба вигрузки — пакетний запуск на ДЕМО-базі.
rem Потрібен, тільки якщо крок 5 інструкції не спрацював.

set "CLIENT=C:\Program Files\BAF\8.3.19.1529\bin\1cv8.exe"
set "BASE=C:\bas\bases\sh_demo"
set "EPF=C:\Users\v09\BAF_Проба.epf"
set "USER_1C=Админ"
set "PASS_1C="

if not exist "%CLIENT%" ( echo НЕ ЗНАЙДЕНО платформу: %CLIENT% & exit /b 1 )
if not exist "%BASE%\1Cv8.1CD" ( echo НЕ ЗНАЙДЕНО демо-базу: %BASE% & exit /b 1 )
if not exist "%EPF%" ( echo НЕ ЗНАЙДЕНО обробку: %EPF% & exit /b 1 )

"%CLIENT%" ENTERPRISE /F"%BASE%" /N"%USER_1C%" /P"%PASS_1C%" /Execute "%EPF%" /C"AUTO" /DisableStartupMessages

echo Код завершення: %ERRORLEVEL%
dir /b /s "%TEMP%\baf_export_proba.json" 2>nul || echo Файл не створено — запустіть обробку вручну за інструкцією.
