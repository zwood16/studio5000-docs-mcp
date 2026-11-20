"""Smoke test: start the server over stdio, list tools, call a few of them.

Usage: S5K_HELP_ROOT=... uv run python scripts/smoke_stdio.py
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "studio5000_docs_mcp.server"],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        print(
            "server:",
            init.server_info.name,
            "| instructions chars:",
            len(init.instructions or ""),
        )
        tools = (await s.list_tools()).tools
        for t in tools:
            print(" tool:", t.name, list(t.input_schema.get("properties", {})))
        calls = [
            ("list_versions", {}),
            ("search_help", {"query": "what sets TON .DN", "limit": 3}),
            ("get_instruction", {"mnemonic": "SSV"}),
            ("get_page", {"page_id": "ait_455128", "max_chars": 800}),
            ("get_page_by_help_id", {"help_id": "131173"}),
            ("browse_toc", {}),
            ("get_breadcrumb", {"page_id": "ait_455128"}),
            ("get_help_statistics", {}),
        ]
        for name, args in calls:
            res = await s.call_tool(name, args)
            text = res.content[0].text
            print(f"\n=== {name}({args}) isError={res.is_error}\n{text[:600]}")
            if res.is_error:
                return 1
        res = await s.call_tool("get_image", {"path": "x.txt"})
        print(f"\n=== get_image(x.txt) isError={res.is_error}")
        if not res.is_error:
            return 1
        # deliberate bad call: failures must be reported as is_error=True
        res = await s.call_tool("get_page", {"page_id": "does-not-exist"})
        print(
            f"\n=== get_page(does-not-exist) isError={res.is_error}\n"
            f"{res.content[0].text[:300]}"
        )
        if not res.is_error:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
