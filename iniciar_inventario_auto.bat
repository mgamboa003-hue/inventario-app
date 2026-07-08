@echo off
echo ===========================================
echo   INICIANDO APP DE INVENTARIO - WINTEC
echo ===========================================
echo.

cd /d "C:\Users\SOLDADORA\Desktop\app_inventario"

echo Activando entorno virtual...
call venv\Scripts\activate

echo Ejecutando aplicacion...
start "" http://127.0.0.1:5002
python app.py

echo.
echo Servidor detenido. Presiona una tecla para cerrar.
pause
