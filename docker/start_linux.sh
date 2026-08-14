#!/bin/bash

# AgentChat 依赖服务启动脚本：MySQL / Redis / MinIO
# 后端和前端请按 docs/delivery/DEPLOYMENT.md 在本机启动

set -e

echo "启动 AgentChat 依赖服务..."

mkdir -p ./mysql/init

docker compose up -d

echo "等待服务启动..."
sleep 10

echo "检查服务状态..."
docker compose ps

echo ""
echo "依赖服务端口："
echo "  MySQL: http://localhost:3307"
echo "  Redis: localhost:6380"
echo "  MinIO API: http://localhost:9002"
echo "  MinIO Console: http://localhost:9003"
echo ""
echo "后端/前端请在本机启动，完整步骤见 docs/delivery/DEPLOYMENT.md"
echo "停止依赖服务：docker compose down"
