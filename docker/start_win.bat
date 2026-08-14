@echo off
setlocal

echo Starting AgentChat dependency services...

if not exist "mysql\init" mkdir mysql\init

docker compose up -d

echo Waiting for services to start...
timeout /t 10 >nul

echo Checking service status...
docker compose ps

echo.
echo Dependency service ports:
echo MySQL: http://localhost:3307
echo Redis: localhost:6380
echo MinIO API: http://localhost:9002
echo MinIO Console: http://localhost:9003
echo.
echo Backend/frontend must be started locally, see docs\delivery\DEPLOYMENT.md
echo Stop dependency services: docker compose down
echo.

pause
