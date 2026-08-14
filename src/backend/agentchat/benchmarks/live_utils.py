from __future__ import annotations

import json
import os
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://127.0.0.1:7860"
DEFAULT_KNOWLEDGE_NAME = "酒店FAQ"
DEFAULT_STATE_PATH = Path(os.environ.get("TEMP", ".")) / "agentchat_live_bench_state.json"
DEFAULT_EMAIL_DOMAIN = "bench.local"
STATE_VERSION = 1


class LiveBenchError(RuntimeError):
    """真实链路评测脚本的通用错误。"""


class ApiError(LiveBenchError):
    """后端接口返回非 200 或 HTTP 错误。"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def load_state(path: Path) -> Dict[str, Any]:
    if not Path(path).exists():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class LiveApi:
    def __init__(
        self,
        base_url: str,
        client: requests.Session,
        token: str = "",
        timeout: Optional[tuple] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client
        self.token = token
        self.timeout = timeout or (30.0, 300.0)

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _raise_api_error(method: str, path: str, response: requests.Response, body: Any) -> None:
        if isinstance(body, dict):
            message = body.get("status_message") or body.get("detail") or body.get("message")
        else:
            message = None
        if not message:
            message = response.text[:300]
        raise ApiError(f"{method} {path} failed (HTTP {response.status_code}): {message}")

    def request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("timeout", self.timeout)
        response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
        try:
            body = response.json()
        except Exception:
            self._raise_api_error(method, path, response, None)
            raise
        if response.status_code >= 400:
            self._raise_api_error(method, path, response, body)
        if not isinstance(body, dict) or body.get("status_code") != 200:
            self._raise_api_error(method, path, response, body)
        return body

    def register(
        self,
        user_name: str,
        user_password: str,
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.request(
            "POST",
            "/api/v1/user/register",
            json={
                "user_name": user_name,
                "user_email": user_email or f"{user_name}@{DEFAULT_EMAIL_DOMAIN}",
                "user_password": user_password,
            },
        )

    def login(self, user_name: str, user_password: str) -> Dict[str, Any]:
        body = self.request(
            "POST",
            "/api/v1/user/login",
            json={"user_name": user_name, "user_password": user_password},
        )
        data = body.get("data") or {}
        token = (data.get("access_token") or "").strip()
        if not token:
            raise ApiError("POST /api/v1/user/login succeeded but returned no access_token")
        self.token = token
        return data

    def ensure_user(
        self,
        user_name: str,
        user_password: str,
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self.login(user_name, user_password)
        except ApiError:
            pass

        try:
            self.register(user_name, user_password, user_email)
        except ApiError as exc:
            message = str(exc)
            if "重复" in message or "exists" in message.lower() or "exist" in message.lower():
                raise LiveBenchError(
                    "评测用户已存在但密码不匹配；请用 --user-name 换一个新用户，"
                    "或提供 --password 恢复原用户的密码"
                ) from exc
            raise
        return self.login(user_name, user_password)

    def select_knowledges(self) -> List[Dict[str, Any]]:
        body = self.request("GET", "/api/v1/knowledge/select")
        return body.get("data") or []

    def create_knowledge(self, knowledge_name: str, knowledge_desc: str) -> None:
        self.request(
            "POST",
            "/api/v1/knowledge/create",
            json={"knowledge_name": knowledge_name, "knowledge_desc": knowledge_desc},
        )

    def find_or_create_knowledge(
        self,
        knowledge_name: str,
        knowledge_desc: str,
    ) -> Dict[str, Any]:
        knowledges = self.select_knowledges()
        for item in knowledges:
            if item.get("name") == knowledge_name:
                return item

        try:
            self.create_knowledge(knowledge_name, knowledge_desc)
        except ApiError as exc:
            message = str(exc)
            if "重复" not in message and "exist" not in message.lower() and "duplicate" not in message.lower():
                raise

        knowledges = self.select_knowledges()
        for item in knowledges:
            if item.get("name") == knowledge_name:
                return item
        raise LiveBenchError(f"知识库 {knowledge_name} 创建后无法在用户列表中找到")

    def upload_file(self, file_name: str, content: bytes) -> str:
        body = self.request(
            "POST",
            "/api/v1/upload",
            files={"file": (file_name, content, "text/plain")},
        )
        sign_url = body.get("data")
        if not isinstance(sign_url, str) or not sign_url:
            raise ApiError("POST /api/v1/upload succeeded but returned no sign_url")
        return sign_url

    def mount_file(self, knowledge_id: str, file_url: str) -> None:
        self.request(
            "POST",
            "/api/v1/knowledge_file/create",
            json={"knowledge_id": knowledge_id, "file_url": file_url},
        )

    def select_knowledge_files(self, knowledge_id: str) -> List[Dict[str, Any]]:
        body = self.request(
            "GET",
            "/api/v1/knowledge_file/select",
            params={"knowledge_id": knowledge_id},
        )
        return body.get("data") or []

    def list_agents(self) -> List[Dict[str, Any]]:
        body = self.request("GET", "/api/v1/agent")
        return body.get("data") or []

    def ensure_agent(
        self,
        name: str,
        description: str,
        system_prompt: str,
        knowledge_ids: List[str],
        enable_memory: bool = True,
        enable_multi_agent: bool = False,
    ) -> Dict[str, Any]:
        for agent in self.list_agents():
            if agent.get("name") == name:
                return agent

        self.request(
            "POST",
            "/api/v1/agent",
            json={
                "name": name,
                "description": description,
                "tool_ids": [],
                "llm_id": None,
                "mcp_ids": [],
                "knowledge_ids": knowledge_ids,
                "agent_skill_ids": [],
                "enable_memory": enable_memory,
                "enable_multi_agent": enable_multi_agent,
                "system_prompt": system_prompt,
                "logo_url": "",
            },
        )

        for agent in self.list_agents():
            if agent.get("name") == name:
                return agent
        raise LiveBenchError(f"Agent {name} created but missing from agent list")

    def create_dialog(
        self,
        name: str,
        agent_id: str,
        agent_type: str = "Agent",
    ) -> Dict[str, Any]:
        body = self.request(
            "POST",
            "/api/v1/dialog",
            json={
                "name": name,
                "agent_id": agent_id,
                "agent_type": agent_type,
            },
        )
        data = body.get("data") or {}
        if not isinstance(data, dict) or not data.get("dialog_id"):
            raise LiveBenchError("POST /api/v1/dialog returned no dialog_id")
        return data

    def list_dialogs(self) -> List[Dict[str, Any]]:
        body = self.request("GET", "/api/v1/dialog/list")
        return body.get("data") or []

    def get_dialog_history(self, dialog_id: str) -> List[Dict[str, Any]]:
        body = self.request(
            "GET",
            "/api/v1/history",
            params={"dialog_id": dialog_id},
        )
        return body.get("data") or []

    def get_usage(
        self,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        delta_days: int = 1,
    ) -> Dict[str, Any]:
        body = self.request(
            "POST",
            "/api/v1/usage",
            json={
                "agent": agent,
                "model": model,
                "delta_days": delta_days,
            },
        )
        return body.get("data") or {}

    def stream_completion(
        self,
        dialog_id: str,
        user_input: str,
        file_url: Optional[str] = None,
        timeout: Optional[tuple] = None,
    ):
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        response = self.client.post(
            f"{self.base_url}/api/v1/completion",
            headers=headers,
            json={
                "user_input": user_input,
                "dialog_id": dialog_id,
                "file_url": file_url,
            },
            stream=True,
            timeout=timeout or self.timeout,
        )
        try:
            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = None
                self._raise_api_error("POST", "/api/v1/completion", response, body)

            for raw in response.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if not raw.startswith("data:"):
                    continue
                payload = raw[len("data:"):].strip()
                if not payload:
                    continue
                yield json.loads(payload)
        finally:
            response.close()
