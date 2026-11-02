@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Radar Vivo

set "PY="
where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
)

if not defined PY (
  echo.
  echo No se encontro Python 3.
  echo Instala Python desde https://www.python.org/downloads/
  echo Marca la opcion "Add python.exe to PATH" durante la instalacion.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creando entorno virtual...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo No se pudo crear el entorno virtual.
    pause
    exit /b 1
  )
)

echo Instalando o verificando dependencias...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
if errorlevel 1 (
  echo No se pudo actualizar pip.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo No se pudieron instalar las dependencias.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" pronost.py
pause
