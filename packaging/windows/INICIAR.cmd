@echo off
setlocal
cd /d "%~dp0"
title Academia - demo de planificacion
if not exist "runtime\python.exe" (
  echo No se encuentra el runtime incluido.
  echo Extrae TODO el ZIP antes de abrir INICIAR.cmd.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" -u "%~dp0launcher.py"
if errorlevel 1 (
  echo.
  echo No se ha podido iniciar Academia.
  echo Revisa el mensaje anterior y el archivo error-inicio.txt si existe.
  pause
)
endlocal
