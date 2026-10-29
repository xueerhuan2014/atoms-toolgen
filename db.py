"""
Supabase 持久化访问模块（PostgREST + service key）

- 所有读写走 Supabase REST API，使用 SUPABASE_SERVICE_KEY（绕过 RLS，仅后端持有）
- 未配置 / 配置为占位符时 ENABLED=False，所有函数优雅降级（返回 None/[]），
  不影响生成与赛马主流程
"""

import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger("mini-tool-gen.db")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
# 兼容误填成 ".../rest/v1" 的情况：统一去掉后缀，后续统一拼 /rest/v1/xxx
if SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[: -len("/rest/v1")]
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def _looks_like_placeholder(value: str) -> bool:
    if not value:
        return True
    lowered = value.lower()
    tokens = ("your", "placeholder", "example", "xxx", "-here", "todo", "change-me")
    return any(tok in lowered for tok in tokens)


ENABLED = bool(
    SUPABASE_URL
    and SUPABASE_SERVICE_KEY
    and not _looks_like_placeholder(SUPABASE_URL)
    and not _looks_like_placeholder(SUPABASE_SERVICE_KEY)
)

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


async def insert_project(prompt: str, final_html: Optional[str],
                         race_versions: Optional[list] = None) -> Optional[dict]:
    """写入一条项目记录，成功返回插入的行（含 id），失败返回 None。

    final_html 允许为空：赛马模式在生成时就落库全部版本，待用户点选后再回填。
    """
    if not ENABLED:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/projects",
                headers=_headers(),
                json={"prompt": prompt, "final_html": final_html,
                      "race_versions": race_versions},
            )
            resp.raise_for_status()
            row = resp.json()[0]
            logger.info(f"Supabase 写入项目: id={row.get('id')}")
            return row
    except Exception as e:
        logger.error(f"Supabase 写入项目失败: {type(e).__name__}: {e}")
        return None


async def list_projects(limit: int = 50) -> Optional[list]:
    """项目列表（不含大字段），按创建时间倒序。未配置返回 None，出错返回 None。"""
    if not ENABLED:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/projects",
                headers=_headers(),
                params={
                    "select": "id,prompt,created_at",
                    "order": "created_at.desc",
                    "limit": str(limit),
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Supabase 读取项目列表失败: {type(e).__name__}: {e}")
        return None


async def get_project(project_id: str) -> Optional[dict]:
    """读取单个项目完整内容（含 final_html / race_versions）。"""
    if not ENABLED:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/projects",
                headers=_headers(),
                params={"select": "*", "id": f"eq.{project_id}"},
            )
            resp.raise_for_status()
            rows = resp.json()
            return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Supabase 读取项目失败: {type(e).__name__}: {e}")
        return None


async def update_project_html(project_id: str, final_html: str) -> bool:
    """连续修改后，把新版本 HTML 写回项目。"""
    if not ENABLED:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.patch(
                f"{SUPABASE_URL}/rest/v1/projects",
                headers=_headers(),
                params={"id": f"eq.{project_id}"},
                json={"final_html": final_html},
            )
            resp.raise_for_status()
            logger.info(f"Supabase 更新项目 HTML: id={project_id}")
            return True
    except Exception as e:
        logger.error(f"Supabase 更新项目失败: {type(e).__name__}: {e}")
        return False


async def insert_message(project_id: str, role: str, content: str) -> bool:
    """写入一条对话消息。"""
    if not ENABLED:
        return False
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/rest/v1/messages",
                headers=_headers(),
                json={"project_id": project_id, "role": role, "content": content},
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Supabase 写入消息失败: {type(e).__name__}: {e}")
        return False


async def list_messages(project_id: str) -> Optional[list]:
    """按时间正序读取某项目的对话消息（不含大字段以外的全部列）。"""
    if not ENABLED:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/messages",
                headers=_headers(),
                params={
                    "select": "id,role,content,created_at",
                    "project_id": f"eq.{project_id}",
                    "order": "created_at.asc",
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Supabase 读取消息失败: {type(e).__name__}: {e}")
        return None
