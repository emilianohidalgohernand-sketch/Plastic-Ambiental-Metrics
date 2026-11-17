@echo off
title Plastic Trade - Servidor Portal Ambiental
cd /d "%~dp0"
echo ===================================================
echo Iniciando servidor Plastic Trade (Portal Ambiental)
echo Accede en tu navegador a: http://127.0.0.1:8000
echo ===================================================
.\.venv\Scripts\python.exe app.py
pause
