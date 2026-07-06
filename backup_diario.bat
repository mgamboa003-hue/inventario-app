@echo off
REM Ejecuta un respaldo de la base de datos usando el Python del venv del proyecto.
cd /d "%~dp0"
call venv\Scripts\activate
python backup_db.py
