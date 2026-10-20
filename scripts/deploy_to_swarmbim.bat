@echo off
chcp 65001 >nul
echo ========================================================
echo   BeeSync.AgentRuntime 1.2.33 部署至 SwarmBIM
echo ========================================================
echo.
echo 正在停止正在运行的 AiConstruction 和 BeeSync.AgentRuntime 进程...
taskkill /F /IM BeeSync.AgentRuntime.exe 2>nul
taskkill /F /IM AiConstruction.exe 2>nul
timeout /t 2 /nobreak >nul

echo 正在同步文件到 D:\SwarmBIM\AiConstruction\BeeSync.AgentRuntime ...
robocopy "C:\Users\jly23\Desktop\OpenManus-main\release\out\1.2.33\BeeSync.AgentRuntime" "D:\SwarmBIM\AiConstruction\BeeSync.AgentRuntime" /MIR /R:2 /W:1

if %ERRORLEVEL% LEQ 3 (
    echo.
    echo ========================================================
    echo   [成功] 已成功部署 BeeSync.AgentRuntime 1.2.33！
    echo ========================================================
) else (
    echo.
    echo [提示] 如果有文件占用，请先在任务管理器中关闭 AiConstruction，然后重新运行本脚本。
)
pause
