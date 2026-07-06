@echo off
REM Registra una tarea programada de Windows que corre backup_diario.bat todos los dias a las 23:00.
REM Ejecutar UNA VEZ como administrador.
schtasks /create /tn "Backup Inventario Wintec" /tr "\"%~dp0backup_diario.bat\"" /sc daily /st 23:00 /f
echo.
echo Tarea programada creada. Puedes verla en el Programador de tareas de Windows.
pause
