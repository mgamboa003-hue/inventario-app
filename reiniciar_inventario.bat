@echo off
cd /d "C:\Users\SOLDADORA\Desktop\app_inventario"
call venv\Scripts\activate

echo Reiniciando app...
taskkill /IM python.exe /F >nul 2>&1
python app.py
pause
