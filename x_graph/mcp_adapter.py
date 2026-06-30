from __future__ import annotations

import json
from typing import Any, Callable

from x_graph.collector import GraphCollector
from x_graph.config import CollectorConfig
from x_graph.x_client import XApiClient


def make_mcp_client(
    call_mcp_tool: Callable[[str, str, dict[str, Any]], Any],
    *,
    api_call_budget: int = 200,
    sleep_seconds: float = 0.5,
) -> XApiClient:
    """Wrap a host MCP `call_mcp_tool(server, tool, args)` function."""

    def mcp_call(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        result = call_mcp_tool("xapi", tool_name, params)
        if isinstance(result, str):
            return json.loads(result)
        if isinstance(result, dict):
            if "errors" in result or "data" in result or "meta" in result:
                return result
            text = result.get("text") or result.get("content")
            if isinstance(text, str):
                return json.loads(text)
        raise TypeError(f"Unexpected MCP tool response type: {type(result)}")

    return XApiClient(
        mcp_call=mcp_call,
        api_call_budget=api_call_budget,
        sleep_seconds=sleep_seconds,
    )


def collect_with_mcp(
    query: str,
    call_mcp_tool: Callable[[str, str, dict[str, Any]], Any],
    *,
    work_dir: str = "data",
    api_budget: int = 50,
    search_pages: int = 2,
    expansions: int = 10,
) -> dict[str, Any]:
    """One-shot collection using xapi MCP tools from an agent host."""
    config = CollectorConfig(
        query=query,
        work_dir=work_dir,
        api_call_budget=api_budget,
        max_search_pages_per_run=search_pages,
        max_expansions_per_run=expansions,
    )
    client = make_mcp_client(call_mcp_tool, api_call_budget=api_budget)
    return GraphCollector(config, client=client).run_once()