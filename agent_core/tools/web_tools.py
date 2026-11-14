"""Tavily search; URL fetching is provided by the native WebFetch capability."""

import os
from typing import Annotated

import httpx
import httpx2
from pydantic import Field
from pydantic_ai import ModelRetry
from tavily import AsyncTavilyClient
from integrations.network import NetworkDenied
from integrations.web_http import fetch_http
from integrations.model_http import ModelTransport
from integrations.network_data import check_data
from tavily.errors import BadRequestError, TimeoutError as TavilyTimeout


async def web_search(query: Annotated[str, Field(min_length=1, max_length=2000)]) -> list[dict]:
    """Search the web with Tavily and return source titles, URLs, and content snippets."""
    try:
        check_data(query, destination="search", category="public")
    except NetworkDenied as error:
        raise ModelRetry(str(error)) from None
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise ModelRetry("网页搜索需要配置 TAVILY_API_KEY。")
    try:
        transport = ModelTransport("https://api.tavily.com", destination="search")
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False) as http, \
                AsyncTavilyClient(api_key=key, client=http) as client:
            response = await client.search(
                query, search_depth="basic", max_results=5,
                include_answer=False, include_raw_content=False, timeout=30,
            )
    except (BadRequestError, TavilyTimeout, httpx.RequestError, NetworkDenied) as exc:
        raise ModelRetry(f"Tavily 搜索失败（{type(exc).__name__}），请调整查询或稍后重试。") from None
    return [{key: item[key] for key in ("title", "url", "content")} for item in response["results"]]


async def web_fetch(url: str) -> dict:
    """Fetch public HTTPS content with checks on each redirect."""
    from markdownify import markdownify

    try:
        final_url, body, media_type = await fetch_http(url)
    except (NetworkDenied, httpx2.HTTPError, OSError) as error:
        message = str(error) if isinstance(error, NetworkDenied) else "网页请求失败"
        raise ModelRetry(message) from None
    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ModelRetry("网页返回了不支持的内容编码") from None
    if "text/html" in media_type:
        import asyncio
        content = await asyncio.to_thread(markdownify, content)
    return {"url": final_url, "title": "", "content": content[:50_000]}
