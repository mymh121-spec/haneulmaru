@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo PC IPv4 주소 후보
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.AddressState -eq 'Preferred' -and $_.IPAddress -ne '127.0.0.1' } | Format-Table InterfaceAlias,IPAddress -AutoSize"
if exist "multidevice_app\bin\haneulmaru.exe" (
    "multidevice_app\bin\haneulmaru.exe" imu
) else (
    "multidevice_app\rust\target\release\haneulmaru.exe" imu
)
pause
