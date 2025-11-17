import asyncio
import warnings
from typing import Any, Dict, List, Literal, Mapping
from loguru import logger

from autogen_core import CancellationToken, Component
from autogen_core.tools import (
    ToolResult,
    ToolSchema,
    Workbench,
)
from pydantic import BaseModel

from autogen_ext.tools.mcp import (
    McpWorkbench,
    McpServerParams,
)

# According to the OpenAI API tool names can contain "Only letters, numbers, '_' and '-' are allowed."
NAMESPACE_SEPARATOR = "-"
# The 'escape' value to use for NAMESPACE_SEPARATOR (specificaly a value outside of the allowable range)
NAMESPACE_ESCAPE = ":"


def escape_tool_name(name: str) -> str:
    """
    Escapes all occurrences of the NAMESPACE_SEPARATOR in the tool name by replacing them with NAMESPACE_ESCAPE.
    """
    return name.replace(NAMESPACE_SEPARATOR, NAMESPACE_ESCAPE)


def unescape_tool_name(name: str) -> str:
    """
    Unescapes all occurrences of NAMESPACE_ESCAPE in the tool name by replacing them with NAMESPACE_SEPARATOR.
    """
    return name.replace(NAMESPACE_ESCAPE, NAMESPACE_SEPARATOR)


class NamedMcpServerParams(BaseModel):
    """A 'namespaced' McpServer"""

    server_name: str
    """The unique name of the server"""
    server_params: McpServerParams
    """The SseServerParams or StdioServerParams for the server."""


class AggregateMcpWorkbenchConfig(BaseModel):
    named_server_params: List[NamedMcpServerParams]


class AggregateMcpWorkbenchState(BaseModel):
    type: Literal["AggregateMcpWorkbenchState"] = "AggregateMcpWorkbenchState"


class AggregateMcpWorkbench(Workbench, Component[AggregateMcpWorkbenchConfig]):
    """
    A workbench that aggregates multiple named MCP servers, providing a unified interface
    to list and call tools from all servers. Each server is given a unique name, and tools
    from each server are namespaced using this name (e.g., "server1.tool_name").

    Args:
        named_server_params (List[NamedMcpServerParams]):
            A list of server configurations, each with a unique name and corresponding MCP server parameters.

    Examples:

        Here is an example of how to use the aggregate workbench with two MCP servers:

        .. code-block:: python

            import asyncio

            from autogen_ext.tools.mcp import StdioServerParams
            from magentic_ui.tools.mcp import AggregateMcpWorkbench, NamedMcpServerParams

            async def main() -> None:
                server1 = NamedMcpServerParams(
                    server_name="fetch",
                    server_params=StdioServerParams(command="uvx", args=["mcp-server-fetch"]),
                )
                server2 = NamedMcpServerParams(
                    server_name="github",
                    server_params=StdioServerParams(command="docker", args=["run", "ghcr.io/github/github-mcp-server"]),
                )
                async with AggregateMcpWorkbench([server1, server2]) as workbench:
                    tools = await workbench.list_tools()
                    print(tools)  # Tool names will be namespaced, e.g., 'fetch.tool1', 'github.tool2'
                    result = await workbench.call_tool("fetch.some_tool", {"url": "https://github.com/"})
                    print(result)

            asyncio.run(main())

    Notes:
        - Tool names are automatically namespaced with their server name (e.g., 'server_name.tool_name').
        - Use the namespaced tool name when calling tools.
        - All server names must be unique.
    """

    component_provider_override = "magentic_ui.tools.mcp.AggregateMcpWorkbench"
    component_config_schema = AggregateMcpWorkbenchConfig

    def __init__(self, named_server_params: List[NamedMcpServerParams], auto_start: bool = True) -> None:
        # Create a copy of server_params
        self._workbenches: Dict[str, McpWorkbench] = {}
        self._auto_start = auto_start
        for params in named_server_params:
            # Check if valid
            if escape_tool_name(params.server_name) != params.server_name:
                raise ValueError(
                    f"Invalid server_name '{params.server_name}'. Server names must not include {NAMESPACE_SEPARATOR} characters."
                )

            if params.server_name in self._workbenches:
                raise ValueError(
                    f"Each server_name in named_server_params must be unique. Encountered duplicate server_name: '{params.server_name}'"
                )
            else:
                self._workbenches[params.server_name] = McpWorkbench(
                    server_params=params.server_params
                )

    @property
    def server_params(self) -> List[NamedMcpServerParams]:
        return [
            NamedMcpServerParams(
                server_name=server_name, server_params=workbench.server_params
            )
            for server_name, workbench in self._workbenches.items()
        ]

    async def list_tools(self) -> List[ToolSchema]:
        schema: List[ToolSchema] = []
        # 跟踪哪些服务器已经失败，避免重复尝试
        failed_servers = set()
        
        for server_name, workbench in self._workbenches.items():
            if server_name in failed_servers:
                continue
            try:
                # 使用超时来避免无限等待
                workbench_tools = await asyncio.wait_for(workbench.list_tools(), timeout=5.0)
                for tool in workbench_tools:
                    # Make a copy of the tool updating the name to be escaped and within this server's 'namespace'
                    tool_name = escape_tool_name(tool["name"])
                    namespaced_tool_name = f"{server_name}{NAMESPACE_SEPARATOR}{tool_name}"
                    namespaced_tool = ToolSchema({**tool, "name": namespaced_tool_name})
                    schema.append(namespaced_tool)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout listing tools from MCP server '{server_name}'. Skipping this server.")
                warnings.warn(
                    f"Timeout listing tools from MCP server '{server_name}'. Skipping this server.",
                    RuntimeWarning,
                    stacklevel=2
                )
                failed_servers.add(server_name)
                continue
            except Exception as e:
                # 如果某个服务器的 list_tools 失败，记录错误但继续处理其他服务器
                # 检查是否是 ExceptionGroup（通过检查是否有 exceptions 属性）
                if hasattr(e, 'exceptions') and hasattr(e, '__class__') and ('ExceptionGroup' in str(type(e)) or 'BaseExceptionGroup' in str(type(e))):
                    # 处理 ExceptionGroup（Python 3.11+）
                    exceptions_list = getattr(e, 'exceptions', [])
                    for sub_exception in exceptions_list:
                        error_msg = str(sub_exception)
                        error_type = type(sub_exception).__name__
                        if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                            warnings.warn(
                                f"Failed to list tools from MCP server '{server_name}': {sub_exception}. "
                                f"MCP server requires authentication or is not available. Skipping this server.",
                                RuntimeWarning,
                                stacklevel=2
                            )
                        else:
                            warnings.warn(
                                f"Failed to list tools from MCP server '{server_name}': {sub_exception}. Skipping this server.",
                                RuntimeWarning,
                                stacklevel=2
                            )
                else:
                    # 处理普通异常
                    error_msg = str(e)
                    error_type = type(e).__name__
                    if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                        warnings.warn(
                            f"Failed to list tools from MCP server '{server_name}': {e}. "
                            f"MCP server requires authentication or is not available. Skipping this server.",
                            RuntimeWarning,
                            stacklevel=2
                        )
                    else:
                        warnings.warn(
                            f"Failed to list tools from MCP server '{server_name}': {e}. Skipping this server.",
                            RuntimeWarning,
                            stacklevel=2
                        )
                # 标记服务器为失败，避免后续重复尝试
                failed_servers.add(server_name)
                # 继续处理其他服务器，不抛出异常
                continue

        return schema

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
        call_id: str | None = None,
    ) -> ToolResult:
        try:
            # Split the server name from teh tool name
            server_name, tool_name = name.split(NAMESPACE_SEPARATOR, 1)
            # Unescape before sending to teh workbench
            tool_name = unescape_tool_name(tool_name)
        except ValueError:
            raise ValueError(
                f"Cannot call tool named '{name}' with AggregateMcpWorkbench. Expected format: '{{server_name}}{NAMESPACE_SEPARATOR}{{tool_name}}'."
            )

        # Get the workbench for that server
        workbench = self._workbenches.get(server_name, None)
        if not workbench:
            raise KeyError(
                f"Cannot call tool named '{tool_name}' on server '{server_name}'. No known servers with that name."
            )

        # Invoke the tool within that namespace
        return await workbench.call_tool(
            tool_name, arguments=arguments, cancellation_token=cancellation_token
        )

    async def start(self) -> None:
        """Start all MCP workbenches, handling errors gracefully."""
        async def start_workbench(server_name: str, workbench: McpWorkbench) -> None:
            """Start a single workbench, catching and logging any errors."""
            try:
                # 使用 asyncio.wait_for 来设置超时，避免无限等待
                # 如果连接失败，应该在几秒内就失败
                await asyncio.wait_for(workbench.start(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"Timeout starting MCP server '{server_name}'. This server will be skipped."
                )
                warnings.warn(
                    f"Timeout starting MCP server '{server_name}'. This server will be skipped.",
                    RuntimeWarning,
                    stacklevel=2
                )
            except Exception as e:
                # 检查是否是 ExceptionGroup（通过检查是否有 exceptions 属性）
                if hasattr(e, 'exceptions') and hasattr(e, '__class__') and ('ExceptionGroup' in str(type(e)) or 'BaseExceptionGroup' in str(type(e))):
                    # 处理 ExceptionGroup（Python 3.11+）
                    exceptions_list = getattr(e, 'exceptions', [])
                    for sub_exception in exceptions_list:
                        error_msg = str(sub_exception)
                        error_type = type(sub_exception).__name__
                        if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                            logger.warning(
                                f"Failed to start MCP server '{server_name}': {sub_exception}. "
                                f"MCP server requires authentication or is not available. This server will be skipped."
                            )
                            warnings.warn(
                                f"Failed to start MCP server '{server_name}': {sub_exception}. "
                                f"MCP server requires authentication or is not available. This server will be skipped.",
                                RuntimeWarning,
                                stacklevel=2
                            )
                        else:
                            logger.warning(
                                f"Failed to start MCP server '{server_name}': {sub_exception}. This server will be skipped."
                            )
                            warnings.warn(
                                f"Failed to start MCP server '{server_name}': {sub_exception}. This server will be skipped.",
                                RuntimeWarning,
                                stacklevel=2
                            )
                else:
                    # 处理普通异常
                    error_msg = str(e)
                    error_type = type(e).__name__
                    if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                        logger.warning(
                            f"Failed to start MCP server '{server_name}': {e}. "
                            f"MCP server requires authentication or is not available. This server will be skipped."
                        )
                        warnings.warn(
                            f"Failed to start MCP server '{server_name}': {e}. "
                            f"MCP server requires authentication or is not available. This server will be skipped.",
                            RuntimeWarning,
                            stacklevel=2
                        )
                    else:
                        logger.warning(
                            f"Failed to start MCP server '{server_name}': {e}. This server will be skipped."
                        )
                        warnings.warn(
                            f"Failed to start MCP server '{server_name}': {e}. This server will be skipped.",
                            RuntimeWarning,
                            stacklevel=2
                        )
                # 不抛出异常，继续处理其他服务器
        
        # 使用 return_exceptions=True 来确保所有任务都完成，即使某些失败
        results = await asyncio.gather(
            *[start_workbench(server_name, workbench) for server_name, workbench in self._workbenches.items()],
            return_exceptions=True
        )
        
        # 检查是否有任何异常（虽然我们已经处理了，但 gather 可能仍然返回异常）
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # 异常已经在 start_workbench 中处理了，这里只是确保不会抛出
                pass

    async def stop(self) -> None:
        await asyncio.gather(
            *[workbench.stop() for workbench in self._workbenches.values()]
        )

    async def reset(self) -> None:
        await asyncio.gather(
            *[workbench.reset() for workbench in self._workbenches.values()]
        )

    async def save_state(self) -> Mapping[str, Any]:
        # TODO: McpWorkbenchState is a 'dummy' class so this is okay for now but we will eventually need to aggregate the sub-workbench states
        return AggregateMcpWorkbenchState().model_dump()

    async def load_state(self, state: Mapping[str, Any]) -> None:
        # TODO: No state to save, so nothing to do. Again will need to fix this if it ever changes in the base McpWorkbench
        pass

    def _to_config(self) -> AggregateMcpWorkbenchConfig:
        named_server_params: List[NamedMcpServerParams] = []
        for server_name, workbench in self._workbenches.items():
            params = NamedMcpServerParams(
                server_name=server_name, server_params=workbench.server_params
            )
            named_server_params.append(params)

        return AggregateMcpWorkbenchConfig(named_server_params=named_server_params)

    @classmethod
    def _from_config(cls, config: AggregateMcpWorkbenchConfig):
        return cls(named_server_params=config.named_server_params)

    def __del__(self) -> None:
        for name, workbench in self._workbenches.items():
            try:
                del workbench
            except Exception as ex:
                msg = f"Caught exception deleting workbench for server named '{name}'. {type(ex).__name__}: {ex}"
                warnings.warn(msg, RuntimeWarning, stacklevel=2)
