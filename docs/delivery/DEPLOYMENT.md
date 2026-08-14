# AgentChat 部署说明

> 当前仓库的 Docker Compose 只启动依赖服务（MySQL、Redis、MinIO），后端和前端在本地运行。
> `docker/docker_config.yaml` 是含真实密钥的本地忽略配置（不提交 git），仅供 `docker/Dockerfile` 构建后端镜像时覆盖到容器内；当前 compose 没有 backend/frontend 服务，不要使用 `docker-compose logs -f backend`。

## 1. 目录结构

```text
AgentChat/
├─ docker/
│  ├─ docker-compose.yml      # MySQL + Redis + MinIO
│  ├─ Dockerfile              # 后端镜像
│  ├─ Dockerfile.frontend     # 前端镜像
│  ├─ docker_config.yaml      # 本地敏感配置，git 忽略
│  ├─ nginx_example.conf      # Nginx 反向代理示例
│  └─ start_linux.sh          # 依赖服务启动脚本
├─ src/backend/               # FastAPI 后端
├─ src/frontend/              # Vue 3 + Vite 前端
└─ .env.example               # 环境变量模板
```

## 2. 启动依赖服务

```powershell
cd docker
Copy-Item ..\.env.example .env
# 编辑 docker/.env，至少填写 MYSQL_ROOT_PASSWORD、MYSQL_PASSWORD、MINIO_ROOT_USER、MINIO_ROOT_PASSWORD
docker compose up -d
docker compose ps
```

依赖服务端口：

| 服务 | 宿主端口 | 说明 |
| --- | --- | --- |
| MySQL | `3307` | 容器内 3306 |
| Redis | `6380` | 容器内 6379 |
| MinIO API | `9002` | 容器内 9000 |
| MinIO 控制台 | `9003` | 容器内 9001 |

## 3. 后端启动

环境变量文件使用项目根目录 `.env`：

```powershell
Copy-Item .env.example .env
```

复制配置模板：

```powershell
Copy-Item src\backend\agentchat\config.yaml.example src\backend\agentchat\config.yaml
```

`config.yaml` 中需要与本机端口对齐：

```yaml
mysql:
  endpoint: "mysql+pymysql://agentchat_user:change-me@localhost:3307/agentchat"
  async_endpoint: "mysql+aiomysql://agentchat_user:change-me@localhost:3307/agentchat"

redis:
  endpoint: "redis://localhost:6380"

storage:
  mode: "minio"
  minio:
    access_key_id: "change-minio-user"
    access_key_secret: "change-minio-password"
    endpoint: "127.0.0.1:9002"
    bucket_name: "agentchat"
    base_url: "http://127.0.0.1:9002/agentchat"
```

推荐使用 conda 环境 `agentchat`：

```powershell
C:\Users\20235\.conda\envs\agentchat\python.exe -m pip install -r src\backend\requirements.txt
cd src\backend
uvicorn agentchat.main:app --port 7860
```

## 4. 前端启动

```powershell
cd src\frontend
npm install
npm run dev
```

前端开发服务器默认 `http://localhost:8090`，`vite.config.ts` 会把 `/api` 代理到 `http://localhost:7860`。

## 5. 验证

- 后端健康检查：`GET http://localhost:7860/health`
- Swagger：`http://localhost:7860/docs`
- 前端：`http://localhost:8090`

## 6. 默认关闭的能力

当前模板与 Agent 默认值遵循“默认保守”原则：

- `enable_memory` 默认 `False`，需要在 Agent 配置中显式开启长记忆；
- `enable_multi_agent` 默认 `False`，多 Agent 编排只在显式开启后构造；
- `rag.enable_elasticsearch` 默认 `False`，未配置 ES 时走向量库混合检索；
- `rag.vector_db.mode` 默认 `chroma`；
- `rag.enable_summary` 默认 `False`。

## 7. 已知边界

- Docker Compose 不包含后端和前端服务，仓库虽有 `Dockerfile`/`Dockerfile.frontend`，但不要按旧 README 的“一键前后端”操作；
- 本机运行读取 `src/backend/agentchat/config.yaml`；镜像构建会把 `docker/docker_config.yaml` 复制为容器内 `agentchat/config.yaml`；
- “离线评测”与“真实线上链路”是两个口径，P2/P3 文档中的数字默认是离线/固定 fixture 结果。
