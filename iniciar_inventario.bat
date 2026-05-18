@echo off
echo.
echo ==========================================
echo   INVENTARIO WINTEC v2
echo ==========================================
echo.

cd /d "%~dp0"

echo Activando entorno virtual...
call venv\Scripts\activate

echo Instalando dependencias...
pip install -r requirements.txt --quiet

echo.
echo Iniciando servidor...
echo.

python app.py

echo.
echo Servidor detenido. Presiona una tecla para cerrar.
pause
