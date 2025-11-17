import asyncio
from typing import (
    AsyncGenerator, 
    List,
    Sequence,
    Optional,
    Mapping,
    Any,
)
from loguru import logger
from pydantic import Field
from autogen_core import CancellationToken, ComponentModel, Component
from autogen_core.models import ChatCompletionClient
from pydantic import BaseModel
from typing_extensions import Self

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.state import BaseState
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    TextMessage,
    MessageFactory,
)

from ..approval_guard import BaseApprovalGuard
from ..tools.mcp import AggregateMcpWorkbench
from autogen_core.tools import ToolSchema
import os


class OpenCreoAgentConfig(BaseModel):
    name: str
    run_id: int
    model_client: ComponentModel
    description: str = ""
    client_ip: Optional[str] = None  # Client IP address for dynamic MCP URL generation


class OpenCreoAgentState(BaseState):
    chat_history: List[BaseChatMessage] = Field(default_factory=list[BaseChatMessage])
    type: str = Field(default="OpenCreoAgentState")


class OpenCreoAgent(BaseChatAgent, Component[OpenCreoAgentConfig]):
    """An agent that opens Creo software via MCP.

    This agent is responsible for calling MCP tools to open Creo software
    on the user's local machine.
    """

    component_type = "agent"
    component_config_schema = OpenCreoAgentConfig
    
    DEFAULT_DESCRIPTION = """
    这是一个用于打开 Creo 软件的智能体。
    它通过 MCP 工具调用用户本地的 Creo 软件。
    """

    def __init__(
        self,
        name: str,
        run_id: int,
        model_client: ChatCompletionClient,
        model_context_token_limit: int = 128000,
        description: str = DEFAULT_DESCRIPTION,
        approval_guard: BaseApprovalGuard | None = None,
        client_ip: Optional[str] = None,
    ) -> None:
        """Initialize the OpenCreoAgent.

        Args:
            name (str): The name of the agent
            model_client (ChatCompletionClient): The language model client to use.
            model_context_token_limit (int, optional): Token limit for model context. Default: 128000.
            description (str, optional): Description of the agent's capabilities. Default: DEFAULT_DESCRIPTION.
            approval_guard (BaseApprovalGuard, optional): Approval guard for actions. Default: None.
            client_ip (str, optional): Client IP address for dynamic MCP URL generation. Default: None.
        """
        super().__init__(name, description)
        self._run_id = run_id
        self._model_client = model_client
        self._approval_guard = approval_guard
        self._did_lazy_init = False
        self._creo_workbench = None
        self._client_ip = client_ip
        self.is_paused = False
        self._paused = asyncio.Event()
        self._chat_history: List[BaseChatMessage | BaseAgentEvent] = []
    
    def _get_creo_mcp_url(self) -> Optional[str]:
        """Get Creo MCP URL, dynamically constructed from client IP if available."""
        # Priority 1: Use client_ip if provided and valid
        if self._client_ip and self._client_ip.strip():
            client_ip = self._client_ip.strip()
            # Basic validation: should not be empty and should not contain protocol
            if client_ip and not client_ip.startswith(('http://', 'https://')):
                creo_mcp_url = f"http://{client_ip}:8080/sse"
                logger.info(f"Using client IP for Creo MCP URL: {creo_mcp_url}")
                return creo_mcp_url
            else:
                logger.warning(f"Invalid client IP format: {self._client_ip}, falling back to environment variable")
        
        # Priority 2: Use environment variable if set
        creo_mcp_url = os.environ.get("CREO_MCP_URL", "").strip()
        if creo_mcp_url:
            # Ensure URL ends with /sse
            if not creo_mcp_url.endswith("/sse"):
                creo_mcp_url = creo_mcp_url.rstrip("/") + "/sse"
            logger.info(f"Using environment variable for Creo MCP URL: {creo_mcp_url}")
            return creo_mcp_url
        
        return None
    
    async def lazy_init(self) -> None:
        """Initialize the Creo MCP workbench if URL is configured."""
        if self._did_lazy_init:
            return
        
        # Initialize Creo MCP workbench if URL is configured
        creo_mcp_url = self._get_creo_mcp_url()
        if creo_mcp_url:
            try:
                from ..tools.mcp import NamedMcpServerParams
                from autogen_ext.tools.mcp import SseServerParams
                
                open_creo_app_tool = NamedMcpServerParams(
                    server_name="creo_server", 
                    server_params=SseServerParams(url=creo_mcp_url)
                )
                self._creo_workbench = AggregateMcpWorkbench(named_server_params=[open_creo_app_tool])
                logger.info(f"Initialized(lazy_init) Creo MCP workbench with URL: {creo_mcp_url}")
            except Exception as e:
                # 处理异常（包括 ExceptionGroup）
                # 检查是否是 ExceptionGroup（通过检查是否有 exceptions 属性）
                if hasattr(e, 'exceptions') and hasattr(e, '__class__') and ('ExceptionGroup' in str(type(e)) or 'BaseExceptionGroup' in str(type(e))):
                    # 处理 ExceptionGroup（Python 3.11+）
                    exceptions_list = getattr(e, 'exceptions', [])
                    for sub_exception in exceptions_list:
                        error_msg = str(sub_exception)
                        error_type = type(sub_exception).__name__
                        if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                            logger.warning(f"Failed to initialize Creo MCP workbench: {sub_exception}. MCP server requires authentication or is not available. Creo tool will not be available.")
                        else:
                            logger.warning(f"Failed to initialize Creo MCP workbench: {sub_exception}. Creo tool will not be available.")
                else:
                    # 处理普通异常
                    error_msg = str(e)
                    error_type = type(e).__name__
                    if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                        logger.warning(f"Failed to initialize Creo MCP workbench: {e}. MCP server requires authentication or is not available. Creo tool will not be available.")
                    else:
                        logger.warning(f"Failed to initialize Creo MCP workbench: {e}. Creo tool will not be available.")
                self._creo_workbench = None
        
        self._did_lazy_init = True

    async def close(self) -> None:
        """Clean up resources used by the agent."""
        logger.info("Closing OpenCreoAgent...")
        await self._model_client.close()

    async def pause(self) -> None:
        """Pause the agent by setting the paused state."""
        self.is_paused = True
        self._paused.set()

    async def resume(self) -> None:
        """Resume the agent by clearing the paused state."""
        self.is_paused = False
        self._paused.clear()

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        """Get the types of messages produced by the agent."""
        return (TextMessage,)

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        """Handle incoming messages and return a single response. Calls the on_messages_stream."""
        response: Response | None = None
        async for message in self.on_messages_stream(messages, cancellation_token):
            if isinstance(message, Response):
                response = message
        assert response is not None
        return response

    async def on_messages_stream(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        """Handle incoming messages and yield responses as a stream."""
        await self.lazy_init()

        if self.is_paused:
            yield Response(
                chat_message=TextMessage(
                    content="打开 Creo 智能体已暂停。",
                    source=self.name,
                    metadata={"internal": "yes"},
                )
            )
            return
        
        self._chat_history.extend(messages)
        inner_messages: List[BaseAgentEvent | BaseChatMessage] = []

        try:
            # 调用 Creo MCP 工具打开用户本地的软件
            if not self._creo_workbench:
                # 如果 workbench 未初始化，尝试获取 URL 并初始化
                creo_mcp_url = self._get_creo_mcp_url()
                if creo_mcp_url:
                    try:
                        from ..tools.mcp import NamedMcpServerParams
                        from autogen_ext.tools.mcp import SseServerParams
                        
                        open_creo_app_server = NamedMcpServerParams(
                            server_name="creo_server", 
                            server_params=SseServerParams(url=creo_mcp_url)
                        )
                        self._creo_workbench = AggregateMcpWorkbench(named_server_params=[open_creo_app_server])
                        logger.info(f"Initialized Creo MCP workbench with URL: {creo_mcp_url}")
                    except Exception as e:
                        # 处理异常（包括 ExceptionGroup）
                        # 检查是否是 ExceptionGroup（通过检查是否有 exceptions 属性）
                        if hasattr(e, 'exceptions') and hasattr(e, '__class__') and ('ExceptionGroup' in str(type(e)) or 'BaseExceptionGroup' in str(type(e))):
                            # 处理 ExceptionGroup（Python 3.11+）
                            exceptions_list = getattr(e, 'exceptions', [])
                            for sub_exception in exceptions_list:
                                error_msg = str(sub_exception)
                                error_type = type(sub_exception).__name__
                                if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                                    logger.warning(f"Failed to initialize Creo MCP workbench: {sub_exception}. MCP server requires authentication or is not available. Creo tool will not be available.")
                                else:
                                    logger.warning(f"Failed to initialize Creo MCP workbench: {sub_exception}. Creo tool will not be available.")
                        else:
                            # 处理普通异常
                            error_msg = str(e)
                            error_type = type(e).__name__
                            if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                                logger.warning(f"Failed to initialize Creo MCP workbench: {e}. MCP server requires authentication or is not available. Creo tool will not be available.")
                            else:
                                logger.warning(f"Failed to initialize Creo MCP workbench: {e}. Creo tool will not be available.")
                        self._creo_workbench = None
            
            # 如果 workbench 已初始化，尝试调用工具
            if self._creo_workbench:
                try:
                    # 获取可用的工具列表
                    tools: List[ToolSchema] = await self._creo_workbench.list_tools()
                    
                    # 查找 open_creo 工具
                    open_creo_tool: ToolSchema | None = None
                    for tool in tools:
                        if "open_creo" in tool.get("name"):
                            open_creo_tool = tool
                            break
                    
                    if open_creo_tool:
                        # 准备工具调用参数
                        tool_params = {
                            "command": "",
                        }
                        
                        # 调用工具打开 Creo 软件
                        logger.info(f"Calling Creo MCP tool: {open_creo_tool.get('name')} with params: {tool_params}")
                        tool_call_result = await self._creo_workbench.call_tool(
                            open_creo_tool.get("name"),
                            tool_params,
                            cancellation_token=cancellation_token,
                        )
                        
                        # 处理工具调用结果
                        tool_result_text = tool_call_result.to_text() if hasattr(tool_call_result, 'to_text') else str(tool_call_result)
                        if tool_result_text:
                            logger.info(f"Creo tool called successfully: {tool_result_text}")
                            yield Response(
                                chat_message=TextMessage(
                                    content=f"已成功调用 Creo MCP 工具打开软件。{tool_result_text}",
                                    source=self.name,
                                    metadata={"finished": "yes"},
                                ),
                                inner_messages=inner_messages,
                            )
                        else:
                            yield Response(
                                chat_message=TextMessage(
                                    content="已成功调用 Creo MCP 工具打开软件。",
                                    source=self.name,
                                    metadata={"finished": "yes"},
                                ),
                                inner_messages=inner_messages,
                            )
                    else:
                        logger.warning("open_creo tool not found in MCP workbench")
                        yield Response(
                            chat_message=TextMessage(
                                content="未找到 open_creo 工具，无法打开 Creo 软件。已跳过此步骤，继续执行后续流程。",
                                source=self.name,
                                metadata={"finished": "yes"},
                            ),
                            inner_messages=inner_messages,
                        )
                except Exception as e:
                    # 处理异常（包括 ExceptionGroup）
                    # 如果工具调用失败，记录错误但不影响主流程
                    errors_handled = False
                    
                    # 检查是否是 ExceptionGroup（通过检查是否有 exceptions 属性）
                    if hasattr(e, 'exceptions') and hasattr(e, '__class__') and ('ExceptionGroup' in str(type(e)) or 'BaseExceptionGroup' in str(type(e))):
                        # 处理 ExceptionGroup（Python 3.11+）
                        exceptions_list = getattr(e, 'exceptions', [])
                        for sub_exception in exceptions_list:
                            error_msg = str(sub_exception)
                            error_type = type(sub_exception).__name__
                            
                            # 检查是否是 HTTP 状态错误（如 401 Unauthorized）
                            if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                                logger.warning(f"Creo MCP authentication error (non-critical): {sub_exception}. MCP server requires authentication or is not available. Skipping this step.")
                                if not errors_handled:
                                    yield Response(
                                        chat_message=TextMessage(
                                            content="Creo MCP 服务器需要认证或不可用，已跳过打开 Creo 软件的步骤，继续执行后续流程。",
                                            source=self.name,
                                            metadata={"finished": "yes"},
                                        ),
                                        inner_messages=inner_messages,
                                    )
                                    errors_handled = True
                            elif "ReadError" in error_msg or "ConnectionError" in error_msg or "ConnectError" in error_msg:
                                logger.warning(f"Creo MCP connection error (non-critical): {sub_exception}. This may be due to network issues or MCP server not running. Skipping this step.")
                                if not errors_handled:
                                    yield Response(
                                        chat_message=TextMessage(
                                            content=f"Creo MCP 连接错误（可能是网络问题或 MCP 服务器未运行），已跳过打开 Creo 软件的步骤，继续执行后续流程。",
                                            source=self.name,
                                            metadata={"finished": "yes"},
                                        ),
                                        inner_messages=inner_messages,
                                    )
                                    errors_handled = True
                        
                        if not errors_handled:
                            # 处理其他类型的错误
                            exceptions_list = getattr(e, 'exceptions', [])
                            first_error = exceptions_list[0] if exceptions_list else None
                            if first_error:
                                error_msg = str(first_error)
                                logger.warning(f"Failed to call Creo MCP tool (non-critical): {first_error}. Skipping this step.")
                                yield Response(
                                    chat_message=TextMessage(
                                        content=f"调用 Creo MCP 工具失败，已跳过打开 Creo 软件的步骤，继续执行后续流程。错误：{error_msg[:100]}",
                                        source=self.name,
                                        metadata={"finished": "yes"},
                                    ),
                                    inner_messages=inner_messages,
                                )
                    else:
                        # 处理普通异常（非 ExceptionGroup）
                        error_msg = str(e)
                        error_type = type(e).__name__
                        
                        # 检查是否是 HTTP 状态错误（如 401 Unauthorized）
                        if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                            logger.warning(f"Creo MCP authentication error (non-critical): {e}. MCP server requires authentication or is not available. Skipping this step.")
                            yield Response(
                                chat_message=TextMessage(
                                    content="Creo MCP 服务器需要认证或不可用，已跳过打开 Creo 软件的步骤，继续执行后续流程。",
                                    source=self.name,
                                    metadata={"finished": "yes"},
                                ),
                                inner_messages=inner_messages,
                            )
                        elif "ReadError" in error_msg or "ConnectionError" in error_msg or "ConnectError" in error_msg:
                            logger.warning(f"Creo MCP connection error (non-critical): {e}. This may be due to network issues or MCP server not running. Skipping this step.")
                            yield Response(
                                chat_message=TextMessage(
                                    content=f"Creo MCP 连接错误（可能是网络问题或 MCP 服务器未运行），已跳过打开 Creo 软件的步骤，继续执行后续流程。",
                                    source=self.name,
                                    metadata={"finished": "yes"},
                                ),
                                inner_messages=inner_messages,
                            )
                        else:
                            logger.warning(f"Failed to call Creo MCP tool (non-critical): {e}. Skipping this step.")
                            yield Response(
                                chat_message=TextMessage(
                                    content=f"调用 Creo MCP 工具失败，已跳过打开 Creo 软件的步骤，继续执行后续流程。错误：{str(e)[:100]}",
                                    source=self.name,
                                    metadata={"finished": "yes"},
                                ),
                                inner_messages=inner_messages,
                            )
            else:
                logger.warning("Creo MCP workbench not initialized. Skipping this step.")
                yield Response(
                    chat_message=TextMessage(
                        content="Creo MCP workbench 未初始化，无法打开 Creo 软件。已跳过此步骤，继续执行后续流程。请检查 CREO_MCP_URL 环境变量或客户端 IP 配置。",
                        source=self.name,
                        metadata={"finished": "yes"},
                    ),
                    inner_messages=inner_messages,
                )
            
        except asyncio.CancelledError:
            # If the task is cancelled, we respond with a message.
            yield Response(
                chat_message=TextMessage(
                    content="当前任务被用户取消。",
                    source=self.name,
                    metadata={"internal": "yes"},
                ),
                inner_messages=inner_messages,
            )
        except Exception as e:
            logger.error(f"Error in OpenCreoAgent: {e}")
            # add to chat history
            self._chat_history.append(
                TextMessage(
                    content=f"打开 Creo 软件时发生错误： {e}",
                    source=self.name,
                )
            )
            yield Response(
                chat_message=TextMessage(
                    content=f"打开 Creo 智能体发生了如下错误： {e}",
                    source=self.name,
                    metadata={"internal": "no"},
                ),
                inner_messages=inner_messages,
            )
    
    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Clear the chat history."""
        self._chat_history.clear()

    def _to_config(self) -> OpenCreoAgentConfig:
        """Convert the agent's state to a configuration object."""
        return OpenCreoAgentConfig(
            name=self.name,
            run_id=self._run_id,
            model_client=self._model_client.dump_component(),
            description=self.description,
            client_ip=self._client_ip,
        )
        
    @classmethod
    def _from_config(cls, config: OpenCreoAgentConfig) -> Self:
        """Create an agent instance from a configuration object."""
        return cls(
            name=config.name,
            run_id=config.run_id,
            model_client=ChatCompletionClient.load_component(config.model_client),
            description=config.description,
            client_ip=config.client_ip,
        )

    async def save_state(self) -> Mapping[str, Any]:
        """
        Save the state of the agent.
        """
        return {
            "chat_history": [msg.dump() for msg in self._chat_history],
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """
        Load the state of the agent.
        """
        # Create message factory for deserialization.
        message_factory = MessageFactory()
        self._chat_history = []
        for msg_data in state["chat_history"]:
            msg = message_factory.create(msg_data)
            assert isinstance(msg, BaseChatMessage)
            self._chat_history.append(msg)
