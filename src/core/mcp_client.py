"""
src/core/mcp_client.py - Synchronous MCP client that exposes the travel MCP
server's tools as LangChain tools.

The agent loop is synchronous, while MCP sessions are async. This client runs
one long-lived MCP session on a background event loop and lets the agent call
tools with plain function calls.

Transports:
    "stdio"  - spawns `python -m src.mcp_server.server` as a subprocess (default)
    "memory" - in-process server over in-memory streams (same MCP protocol; used by tests)
"""

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MCPToolClient:

    def __init__(self, transport: str = "stdio", connect_timeout: float = 60.0):
        if transport not in {"stdio", "memory"}:
            raise ValueError(f"Unsupported MCP transport: {transport}")
        self.transport = transport
        self.tools: Dict[str, BaseTool] = {}
        self.call_count = 0

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-client", daemon=True)
        self._thread.start()

        self._ready = threading.Event()
        self._shutdown: Optional[asyncio.Event] = None
        self._startup_error: Optional[BaseException] = None
        self._session_future = asyncio.run_coroutine_threadsafe(self._run_session(), self._loop)

        if not self._ready.wait(connect_timeout):
            self.close()
            raise TimeoutError("Timed out connecting to the travel MCP server.")
        if self._startup_error is not None:
            self.close()
            raise RuntimeError(f"Failed to start travel MCP server: {self._startup_error}") from self._startup_error

    # The session's context managers must be entered and exited in the same
    # task, so a single coroutine owns the whole session lifetime.
    async def _run_session(self) -> None:
        self._shutdown = asyncio.Event()
        try:
            if self.transport == "stdio":
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "src.mcp_server.server"],
                    cwd=str(PROJECT_ROOT),
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await self._serve(session)
            else:
                from mcp.shared.memory import create_connected_server_and_client_session
                from src.mcp_server.server import build_server

                async with create_connected_server_and_client_session(build_server()) as session:
                    await self._serve(session)
        except BaseException as exc:  # surface startup failures to the constructor
            if not self._ready.is_set():
                self._startup_error = exc
                self._ready.set()
            else:
                raise

    async def _serve(self, session: ClientSession) -> None:
        tools = await load_mcp_tools(session, server_name="travel-tools")
        self.tools = {tool.name: tool for tool in tools}
        self._ready.set()
        await self._shutdown.wait()

    # ------------------------------------------------------------------

    @property
    def tool_names(self) -> List[str]:
        return list(self.tools)

    def describe_tools(self) -> List[Dict[str, Any]]:
        """Name, description, and argument schema for each discovered tool."""
        described = []
        for tool in self.tools.values():
            schema = tool.args_schema if isinstance(tool.args_schema, dict) else tool.get_input_schema().model_json_schema()
            described.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": list((schema or {}).get("properties", {})),
                "required": list((schema or {}).get("required", [])),
            })
        return described

    def call(self, name: str, **arguments: Any) -> Any:
        """Invoke an MCP tool through its LangChain wrapper and decode the JSON result."""
        tool = self.tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown MCP tool: {name}")
        arguments = {key: value for key, value in arguments.items() if value is not None}
        self.call_count += 1
        future = asyncio.run_coroutine_threadsafe(tool.ainvoke(arguments), self._loop)
        return self._decode(future.result())

    @staticmethod
    def _decode(content: Any) -> Any:
        if isinstance(content, list):
            texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
            content = "".join(texts)
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"status": "error", "message": content}
        return content

    def close(self) -> None:
        if self._loop.is_closed():
            return
        if self._shutdown is not None:
            self._loop.call_soon_threadsafe(self._shutdown.set)
            try:
                self._session_future.result(timeout=10)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._loop.is_running():
            self._loop.close()

    def __enter__(self) -> "MCPToolClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class MCPServiceProxy:
    """Presents a group of MCP tools as a search/book/cancel service object."""

    def __init__(self, client: MCPToolClient, **method_to_tool: str):
        self._client = client
        self._method_to_tool = method_to_tool

    def __getattr__(self, method: str):
        method_to_tool = self.__dict__.get("_method_to_tool", {})
        if method not in method_to_tool:
            raise AttributeError(method)
        tool_name = method_to_tool[method]

        def invoke(*args: Any, **kwargs: Any) -> Any:
            # Positional args map onto the MCP tool's parameters in schema order,
            # so proxies behave like the original tool classes (e.g. cancel(booking_id)).
            if args:
                schema = next(t for t in self._client.describe_tools() if t["name"] == tool_name)
                params = schema["parameters"]
                if len(args) > len(params):
                    raise TypeError(f"{tool_name} takes {len(params)} arguments but {len(args)} were given")
                kwargs = {**dict(zip(params, args)), **kwargs}
            return self._client.call(tool_name, **kwargs)

        return invoke
