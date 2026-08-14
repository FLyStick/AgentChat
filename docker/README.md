# AgentChat 依赖服务 Docker 说明

## 现状

当前 `docker/docker-compose.yml` 只负责启动三个依赖服务：

- MySQL
- Redis
- MinIO

后端 FastAPI 和前端 Vue 项目在宿主机本地运行，不提供 backend/frontend 容器服务。

## 快速启动

```powershell
cd docker
Copy-Item ..\.env.example .env
# 编辑 docker/.env，填写 MYSQL_ROOT_PASSWORD、MYSQL_PASSWORD、MINIO_ROOT_USER、MINIO_ROOT_PASSWORD
docker compose up -d
docker compose ps
```

## 宿主端口

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| MySQL | `3307` | 容器内 3306 |
| Redis | `6380` | 容器内 6379 |
| MinIO API | `9002` | 容器内 9000 |
| MinIO 控制台 | `9003` | 容器内 9001 |

## 常用操作

```bash
docker compose ps
docker compose logs -f mysql
docker compose logs -f redis
docker compose logs -f minio
docker compose down
```

## 后端与前端启动

完整命令、环境变量和配置示例见 [docs/delivery/DEPLOYMENT.md](../docs/delivery/DEPLOYMENT.md)。

## 不要照抄旧文档

- `docker/docker_config.yaml` 是本地敏感配置，已被 git 忽略；`Dockerfile` 构建后端镜像时会覆盖到容器内；
- 当前没有 `docker-compose logs -f backend` / `frontend` 服务；
- 不要执行 `docker compose up --build -d` 后认为平台已经启动，它只启动了依赖服务。
