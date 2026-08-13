"""Shared pytest setup for offline unit tests.

The unit tests exercise orchestration and RAG logic without touching OSS,
Chroma, Milvus, or the MySQL server, so those client modules are replaced
with lightweight stubs before application modules import them.
"""

import sys
import types

from agentchat.schemas.common import MultiModels, Rag, Tools
from agentchat.settings import app_settings


app_settings.mysql = {
    "endpoint": "mysql+pymysql://agentchat:agentchat@127.0.0.1:3306/agentchat",
    "async_endpoint": "mysql+aiomysql://agentchat:agentchat@127.0.0.1:3306/agentchat",
}
app_settings.default_config = {"dialog_summary_cutoff_tokens": 2000}
app_settings.tools = Tools(
    google={"api_key": "test"},
    tavily={"api_key": "test"},
    weather={"api_key": "test", "endpoint": "https://example.invalid"},
    delivery={"api_key": "test", "endpoint": "https://example.invalid"},
    bocha={"api_key": "test", "endpoint": "https://example.invalid"},
)
app_settings.multi_models = MultiModels()
app_settings.rag = Rag(
    retrival={"min_score": 0.2, "top_k": 5, "rerank_threshold": None},
    vector_db={"mode": "chroma"},
)

# Import-time storage client construction would hit OSS/MinIO or require
# pycryptodome. The tools under test only need the symbol to exist.
storage_module = types.ModuleType("agentchat.services.storage")
storage_module.storage_client = object()
sys.modules["agentchat.services.storage"] = storage_module

# Import-time vector store construction would create a Chroma directory or
# try to reach Milvus. The RAG unit tests replace retrieval/rerank anyway.
vector_stores_module = types.ModuleType("agentchat.services.rag.vector_stores")
vector_stores_module.milvus_client = object()
sys.modules["agentchat.services.rag.vector_stores"] = vector_stores_module

# Query rewrite builds a ChatOpenAI client at import time, which pulls in
# incomplete HTTP stack deps in offline test environments. The unit tests
# always monkeypatch or disable query rewrite.
query_write_module = types.ModuleType("agentchat.services.rewrite.query_write")
query_write_module.query_rewriter = object()
sys.modules["agentchat.services.rewrite.query_write"] = query_write_module

# pdf2docx pulls in PyMuPDF at import time, which is not always installed in
# the offline test environment. Unit tests never convert documents.
pdf2docx_module = types.ModuleType("pdf2docx")
pdf2docx_module.Converter = object()
sys.modules["pdf2docx"] = pdf2docx_module
