import asyncio
from pathlib import Path
import shutil
from typing import (
    AsyncGenerator, 
    List,
    Sequence,
    Optional,
    Dict,
    Any,
    Mapping,
    Callable,
    ClassVar,
    cast
)
from typing_extensions import Annotated
import json, os
from loguru import logger
from datetime import datetime
from pydantic import Field
from autogen_core import CancellationToken, ComponentModel, Component
from autogen_core.models import (
    ChatCompletionClient,
    UserMessage,
    SystemMessage,
)
from pydantic import BaseModel
from typing_extensions import Self

from autogen_agentchat.agents import BaseChatAgent
from autogen_core.model_context import (
    TokenLimitedChatCompletionContext,
)
from autogen_agentchat.base import Response
from autogen_agentchat.state import BaseState
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    TextMessage,
    ToolCallExecutionEvent,
    MessageFactory,
    ToolCallRequestEvent,
    BaseTextChatMessage,
)
from autogen_core import FunctionCall
from autogen_core.models import (
    LLMMessage,
    FunctionExecutionResult,
    AssistantMessage,
    FunctionExecutionResultMessage,
    CreateResult,
)

from autogen_core.tools import FunctionTool

from ._utils import notify_to_download, read_file
from ..utils import thread_to_context

from ..approval_guard import BaseApprovalGuard
from ..guarded_action import ApprovalDeniedError
from ..tools.mcp import AggregateMcpWorkbench
from autogen_core.tools import ToolSchema
from ..teams.orchestrator._utils import extract_json_from_string
# import logging
# from autogen_agentchat import logger_NAME


class MaterialSelectionAgentConfig(BaseModel):
    name: str
    run_id: int
    model_client: ComponentModel
    description: str = ""
    max_reties: int = 3
    summarize_output: bool = False
    work_root: str
    work_relative_dir: str
    bind_root: str
    bind_relative_dir: str
    client_ip: Optional[str] = None  # Client IP address for dynamic MCP URL generation
    # Optionally add code_executor config if needed


class MaterialSelectionAgentState(BaseState):
    chat_history: List[BaseChatMessage] = Field(default_factory=list[BaseChatMessage])
    type: str = Field(default="MaterialSelectionAgentState")


class MaterialSelectionAgent(BaseChatAgent, Component[MaterialSelectionAgentConfig]):
    """A material selection agent capable of generating BOM list (material list) from specification and circuit diagram.

    The agent reads technical specification documents and circuit diagram files,
    selects appropriate materials based on requirements, and generates BOM list files.
    It maintains a chat history and can be paused/resumed during generation.
    """

    component_type = "agent"
    component_config_schema = MaterialSelectionAgentConfig
    
    DEFAULT_DESCRIPTION = """
    
    这是一个可以生成物料清单（BOM list）的智能体，在电气设计工作流程中发挥重要作用。
    它依据技术规格说明书和电路拓扑图（包含电路描述），选择合适的物料，生成符合需求参数要求的电气物料清单并保存对应的文件。
    成功生成物料清单后，该智能体返回物料参数列表和物料清单文件的保存路径。
    """

    system_prompt_template: ClassVar[str] = """
    你是{name}, 一个使用工具进行物料选型和物料清单生成的智能体，但是你本身不做任何主观物料选型决策。

    今天的日期是:{date_today}
    
    ## 工作原则
    在物料选型过程中，你运用生成式(generative)算法工具，基于技术规格说明书和电路拓扑图（包含电路描述），选择合适的物料，生成符合需求参数要求的电气物料清单（BOM list）。并保存为对应的文件。
    物料清单的生成是一个迭代的过程，你需要和用户进行多轮交互，直到用户确认并同意生成的物料清单符合他的要求。在每次交互中，你只能调用给定的工具来生成物料清单，不要自己生成物料清单。
    
    ## 文件查找
    技术规格说明书文件和电路拓扑图文件会自动从当前会话路径中查找：
    - 技术规格说明书文件：在当前会话路径下查找包含"技术规格说明书"字样的.docx文档
    - 电路拓扑图文件：在当前会话路径下查找图片文件（.jpg, .jpeg, .png, .dwg, .pdf等格式）
    
    为了帮助用户更好地生成符合要求的物料清单，首先思考如下问题:
    1. 当前会话路径下是否有包含"技术规格说明书"字样的.docx文档？如果有，直接调用工具生成物料清单。工具会自动查找该文件。
    2. 当前会话路径下是否有电路拓扑图文件（图片文件）？如果有，直接调用工具生成物料清单。工具会自动查找该文件。
    3. 指令中是否明确提到了电路描述？ 如果提供了，使用该电路描述。
    4. 是否已经有物料清单文件了(包含用户提供的或者之前迭代过程中生成的)？ 如果答案是否定的，直接根据用户的请求生成物料清单文件。否则，根据用户的回复做出合理的回答或者动作。
    5. 用户是否已经确认生成的物料清单符合他的要求(额外地，"继续"或者"下一步"等同义表达也表示用户已经确认生成的物料清单符合他的要求)？ 如果答案是肯定的，输出已经满足用户需求的物料清单。否则，根据用户的回复做出合理的回答或者动作。 
    
    **重点注意**
    - 你只能调用给定的工具来生成物料清单，不要自己生成物料清单。
    - 不要添加任何主观意见，不要添加任何解释，不要添加任何说明，不要添加任何备注。
    - 对于没有提到的信息，一定不能杜撰！比如，如果没有技术规格说明书或电路拓扑图文件，一定不能杜撰文件，如实回答或者不要提及。
    - 当调用_generate_bom_list_file工具时，specification_path和circuit_diagram_path参数可以为空字符串，工具会自动在当前会话路径中查找相应的文件。
    - 生成物料清单时，一定需要调用生成式工具，不然不能生成新的物料清单文件。
    - 物料选型需要基于技术规格说明书中的参数要求和电路拓扑图中的电路结构来进行。
    
    ## 输出格式
    你的输出可以是请求调用工具或回复用户物料清单的生成情况。
    
    如果不需要调用工具，输出为JSON格式。严格遵循以下JSON schema格式，不要输出JSON以外的任何内容:
    ```json
    {{
        "complete": 用户是否已经确认生成的物料清单符合他的要求？ 如果是取"true"，否则"false",
        "message": 给用户的回复。如果`complete`为"false"总是额外地询问用户是否同意已经生成的物料清单，否则告知用户任务已经完成。如果有生成物料清单文件，同时包含物料清单文件的保存路径。
        "specification_path": 技术规格说明书文件的路径（工具会自动查找，可以为空字符串）,
        "circuit_diagram_path": 电路拓扑图文件的路径（工具会自动查找，可以为空字符串）,
        "bomlist_path": 物料清单（BOM list）输出文件的保存路径
    }}
    ```
    
    **重点注意**
    - 输出最终满足用户需求的物料清单时，请在`message`字段中同时表明你的工作已经完成。
    - 用简洁的语句回复用户，但是必须包含必要的信息，比如，你不能简单地回复"任务已经完成"，而是要告知用户任务已经完成，并且告知用户物料清单文件的保存路径。
    - `specification_path`、`circuit_diagram_path`和`bomlist_path`字段只能包含文件路径，不要有任何解释说明或者其他文字。
    - 对于没有提到的信息，一定不能杜撰！比如，如果没有生成文件，一定不能杜撰一个文件路径，如实回答或者不要提及。
    """

    RESPONSE_TEMPLATE = """
    {message}
    
    """

    def __init__(
        self,
        name: str,
        run_id: int,
        model_client: ChatCompletionClient,
        work_root: Path,
        work_relative_dir: Path,
        bind_root: Path,
        bind_relative_dir: Path,
        model_context_token_limit: int = 128000,
        description: str = DEFAULT_DESCRIPTION,
        max_reties: int = 2,
        summarize_output: bool = False,
        approval_guard: BaseApprovalGuard | None = None,
        client_ip: Optional[str] = None,
    ) -> None:
        """Initialize the CodingAgent.

        Args:
            name (str): The name of the agent
            model_client (ChatCompletionClient): The language model client to use.
            coding_tools: the NamedMcpServerParams specifying the coding provider MCP server.
            work_root (Path): Working root directory of this run session.
            work_relative_dir (Path): Directory relative to {work_root} to save generated files in the local filesystem. 
            bind_root (Path): Working root directory of this run session inside Docker container.
            bind_relative_dir (Path): Relative directory to save generated code files inside Docker container.
            code_manager (CodeExecutor): It does not execute code curerently, 
                but utilize the code_manager to run the coding provider MCP server. Default: None.
            coding_provider (str): The name of the coding tool provided by the code_manger MCP. Default: "gemini_cli".
            description (str, optional): Description of the agent's capabilities. Default: DEFAULT_DESCRIPTION.
            max_reties (int, optional): Maximum number of tring generate json response. Default: 2.
            summarize_output (bool, optional): Whether to summarize code execution results. Default: False.
        """
        super().__init__(name, description)
        self._run_id = run_id
        self._model_client = model_client
        self._model_context = TokenLimitedChatCompletionContext(
            model_client, token_limit=model_context_token_limit
        )
        self._chat_history: List[BaseChatMessage | BaseAgentEvent] = []
        self._max_reties = max_reties
        self._summarize_output = summarize_output
        self.is_paused = False
        self._paused = asyncio.Event()
        self._approval_guard = approval_guard
        self._did_lazy_init = False
        self._cleanup_work_dir = False
        self._work_root = work_root
        self._work_relative_dir = work_relative_dir
        self._bind_root = bind_root
        self._bind_relative_dir = bind_relative_dir
        self._simulink_workbench = None
        self._client_ip = client_ip
    
        self._tools = self._setup_tools()
    
    def _setup_tools(self) -> List[FunctionTool]:
        """
        Setup tools used in orchestrator
        """
        return [
            FunctionTool(self._generate_bom_list_file, 
                             description=self._generate_bom_list_file.__doc__ or ""),
            FunctionTool(self._read_file, description=self._read_file.__doc__ or "")
        ]
    
    def _get_simulink_mcp_url(self) -> Optional[str]:
        """Get Simulink MCP URL, dynamically constructed from client IP if available."""
        # Priority 1: Use client_ip if provided and valid
        if self._client_ip and self._client_ip.strip():
            # Validate that client_ip is not localhost/127.0.0.1 (unless that's what we want)
            # For now, we'll use it as-is since the server needs to connect to the client
            # Construct URL from client IP: http://<client_ip>:8080/sse
            # Strip any whitespace and ensure it's a valid IP/hostname
            client_ip = self._client_ip.strip()
            # Basic validation: should not be empty and should not contain protocol
            if client_ip and not client_ip.startswith(('http://', 'https://')):
                simulink_mcp_url = f"http://{client_ip}:8080/sse"
                logger.info(f"Using client IP for Simulink MCP URL: {simulink_mcp_url}")
                return simulink_mcp_url
            else:
                logger.warning(f"Invalid client IP format: {self._client_ip}, falling back to environment variable")
        
        # Priority 2: Use environment variable if set
        simulink_mcp_url = os.environ.get("SIMULINK_MCP_URL", "").strip()
        if simulink_mcp_url:
            # Ensure URL ends with /sse
            if not simulink_mcp_url.endswith("/sse"):
                simulink_mcp_url = simulink_mcp_url.rstrip("/") + "/sse"
            logger.info(f"Using environment variable for Simulink MCP URL: {simulink_mcp_url}")
            return simulink_mcp_url
        
        return None
    
    async def lazy_init(self) -> None:
        """Initialize the code executor if it has a start method.

        This method is called after initialization to set up any async resources
        needed by the code executor.
        """
        if self._did_lazy_init:
            return
        
        # Initialize Simulink MCP workbench if URL is configured
        simulink_mcp_url = self._get_simulink_mcp_url()
        if simulink_mcp_url:
            try:
                from ..tools.mcp import NamedMcpServerParams
                from autogen_ext.tools.mcp import SseServerParams
                
                open_simulink_app_tool = NamedMcpServerParams(
                    server_name="simulink_server", 
                    server_params=SseServerParams(url=simulink_mcp_url)
                )
                self._simulink_workbench = AggregateMcpWorkbench(named_server_params=[open_simulink_app_tool])
                logger.info(f"Initialized(lazy_init) Simulink MCP workbench with URL: {simulink_mcp_url}")
            except Exception as e:
                # 如果初始化失败，记录错误但不影响主流程
                # 检查是否是 ExceptionGroup（通过检查是否有 exceptions 属性）
                if hasattr(e, 'exceptions') and hasattr(e, '__class__') and ('ExceptionGroup' in str(type(e)) or 'BaseExceptionGroup' in str(type(e))):
                    # 处理 ExceptionGroup（Python 3.11+）
                    exceptions_list = getattr(e, 'exceptions', [])
                    for sub_exception in exceptions_list:
                        error_msg = str(sub_exception)
                        error_type = type(sub_exception).__name__
                        if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                            logger.warning(f"Failed to initialize Simulink MCP workbench: {sub_exception}. MCP server requires authentication or is not available. Simulink tool will not be available.")
                        else:
                            logger.warning(f"Failed to initialize Simulink MCP workbench: {sub_exception}. Simulink tool will not be available.")
                else:
                    # 处理普通异常
                    error_msg = str(e)
                    error_type = type(e).__name__
                    if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                        logger.warning(f"Failed to initialize Simulink MCP workbench: {e}. MCP server requires authentication or is not available. Simulink tool will not be available.")
                    else:
                        logger.warning(f"Failed to initialize Simulink MCP workbench: {e}. Simulink tool will not be available.")
                self._simulink_workbench = None
        
        self._did_lazy_init = True

    async def close(self) -> None:
        """Clean up resources used by the agent.

        This method:
        - Stops the code executor
        - Removes the work directory if it was created
        - Closes the model client
        """
        logger.info("Closing MaterialSelectionAgent...")
        # Remove the work directory if it was created.
        if self._cleanup_work_dir and (self._work_root / self._work_relative_dir).exists():
            await asyncio.to_thread(shutil.rmtree, self._work_root / self._work_relative_dir)
        # Close the model client.
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
        """Handle incoming messages and yield responses as a stream. Append the request to agents chat history."""
        await self.lazy_init()

        if self.is_paused:
            yield Response(
                chat_message=TextMessage(
                    content="物料选型智能体已暂停。",
                    source=self.name,
                    metadata={"internal": "yes"},
                )
            )
            return
        self._chat_history.extend(messages)
        inner_messages: List[BaseAgentEvent | BaseChatMessage] = []

        # Set up the cancellation token for the code execution.
        code_execution_token = CancellationToken()

        # Cancel the code execution if the handler's cancellation token is set.
        cancellation_token.add_callback(lambda: code_execution_token.cancel())

        # Set up background task to monitor the pause event and cancel the code execution if paused.
        async def monitor_pause() -> None:
            await self._paused.wait()
            code_execution_token.cancel()

        monitor_pause_task = asyncio.create_task(monitor_pause())

        try:
            async for msg in self._generate_bom_list(
                self.name, 
                self._model_client, 
                self._bind_relative_dir, 
                self._max_reties, 
                code_execution_token, 
                ):
                
                self._chat_history.append(msg)
                inner_messages.append(msg)
                finished = ""
                if isinstance(
                    msg,
                    (
                        BaseTextChatMessage,
                        ToolCallRequestEvent,
                        ToolCallExecutionEvent,
                        # ToolCallSummaryMessage,
                    ),
                ):
                    metadata = getattr(msg, "metadata", {})
                    if "internal" not in metadata:
                        # If internal is not set, set it to "no"
                        metadata = {
                            **metadata,
                            # Display in UI
                            "internal": "no",
                            # Part of a plan step
                            "type": "progress_message",
                        }
                    else:
                        metadata = {
                            **metadata,
                            # Part of a plan step
                            "type": "progress_message",
                        }

                    finished = metadata.get("finished", "")
                    setattr(msg, "metadata", metadata)

                if finished:
                    assert isinstance(msg, TextMessage)
                    yield Response(chat_message=msg, inner_messages=inner_messages)
                else:
                    yield msg
            
        except ApprovalDeniedError:
            # If the user denies the approval, we respond with a message.
            yield Response(
                chat_message=TextMessage(
                    content="用户拒绝了执行物料选型。",
                    source=self.name,
                    metadata={"internal": "no"},
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
            logger.error(f"Error in MaterialSelectionAgent: {e}")
            # add to chat history
            self._chat_history.append(
                TextMessage(
                    content=f"生成物料清单时发生错误： {e}",
                    source=self.name,
                )
            )
            yield Response(
                chat_message=TextMessage(
                    content=f"物料选型智能体发生了如下错误： {e}",
                    source=self.name,
                    metadata={"internal": "no"},
                ),
                inner_messages=inner_messages,
            )
        finally:
            # Cancel the monitor task.
            try:
                monitor_pause_task.cancel()
                await monitor_pause_task
            except asyncio.CancelledError:
                pass
    
    async def _generate_bom_list(
        self,
        agent_name: str,
        model_client: ChatCompletionClient,
        bind_relative_dir: Path,
        max_json_retries: int,
        cancellation_token: CancellationToken,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage, None]:
        """Generate BOM list (material list) from specification and circuit diagram.

        It calls the model to generate material list based on the system prompt and the thread of messages.

        When the cancellation token is set, the execution will stop.
        Args:
            agent_name (str): The name of the agent.
            model_client (ChatCompletionClient): The model client to use for generation.
            bind_relative_dir (Path): The relative directory for file operations.
            max_json_retries (int): The maximum number of retry rounds to perform.
            cancellation_token (CancellationToken): The cancellation token to stop execution.

        Yields:
            TextMessage: The intermediate messages generated by the model and executor.
            BaseAgentEvent: Tool call events.

        Raises:
            ApprovalDeniedError: If the user denies the approval for the BOM list generation request.
        """
        # The list of new messages to be added to the thread.
        # Add system prompt as the last message before generation

        context = self._thread_to_context()
        # the delegator only take as input the last message to analyze
        # the historical messages are ignored
        # last_message = context[-1]
        # delegator_context = [SystemMessage(content=system_prompt), last_message]

        # Re-initialize model context to meet token limit quota
        try:
            await self._model_context.clear()
            for msg in context:
                await self._model_context.add_message(msg)
            token_limited_context = await self._model_context.get_messages()
        except Exception:
            token_limited_context = context
        
        
        # preprocess the user request and extract coding tool parameters
        exception_message = ""
        if exception_message:
            await self._model_context.add_message(
                UserMessage(content=exception_message, source=agent_name)
            )
        try:       
            response = None
            # While loop for tool calls
            while True:
                token_limited_context = await self._model_context.get_messages()
                response = await self._get_json_response(
                    token_limited_context,
                    self.validate_output_json,
                    cancellation_token,
                    max_tries=max_json_retries
                )
                
                # is the respone a ToolCallEvent? not JSON
                if isinstance(response, CreateResult):
                    assert isinstance(response.content, List)
                    yield ToolCallRequestEvent(content=response.content, source=self._name)
                    await self._model_context.add_message(AssistantMessage(content=response.content, source=self._name))
                    tool_call_results = await asyncio.gather(*[self._execute_tool_call(function_call, cancellation_token) for function_call in response.content])    
                    await self._model_context.add_message(FunctionExecutionResultMessage(content=tool_call_results))
                    yield ToolCallExecutionEvent(content=tool_call_results, source=self._name, metadata={"internal": "yes"})
                else:
                    break
            
            assert response
            # Ensure response is a dict before accessing its keys
            if not isinstance(response, dict):
                raise RuntimeError(f"响应类型错误，期望 dict，实际为 {type(response)}")
            
            # Handle complete field: it might be string "true"/"false" or boolean
            complete_value = response.get("complete", False)
            if isinstance(complete_value, str):
                complete = complete_value.lower() in ("true", "1", "yes")
            else:
                complete = bool(complete_value)
            
            # 在需要用户确认之前（complete=false），先调用 Simulink MCP 工具打开软件
            # 这样用户可以在软件中查看物料清单后再确认
            if not complete:
                # 调用 Simulink MCP 工具打开用户本地的软件
                if not self._simulink_workbench:
                    # 如果 workbench 未初始化，尝试获取 URL 并初始化
                    simulink_mcp_url = self._get_simulink_mcp_url()
                    if simulink_mcp_url:
                        try:
                            from ..tools.mcp import NamedMcpServerParams
                            from autogen_ext.tools.mcp import SseServerParams
                            
                            open_simulink_app_server = NamedMcpServerParams(
                                server_name="simulink_server", 
                                server_params=SseServerParams(url=simulink_mcp_url)
                            )
                            self._simulink_workbench = AggregateMcpWorkbench(named_server_params=[open_simulink_app_server])
                            logger.info(f"Initialized Simulink MCP workbench with URL: {simulink_mcp_url}")
                        except Exception as e:
                            # 如果初始化失败，记录错误但不影响主流程
                            # 检查是否是 ExceptionGroup（通过检查是否有 exceptions 属性）
                            if hasattr(e, 'exceptions') and hasattr(e, '__class__') and ('ExceptionGroup' in str(type(e)) or 'BaseExceptionGroup' in str(type(e))):
                                # 处理 ExceptionGroup（Python 3.11+）
                                exceptions_list = getattr(e, 'exceptions', [])
                                for sub_exception in exceptions_list:
                                    error_msg = str(sub_exception)
                                    error_type = type(sub_exception).__name__
                                    if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                                        logger.warning(f"Failed to initialize Simulink MCP workbench: {sub_exception}. MCP server requires authentication or is not available. Simulink tool will not be available.")
                                    else:
                                        logger.warning(f"Failed to initialize Simulink MCP workbench: {sub_exception}. Simulink tool will not be available.")
                            else:
                                # 处理普通异常
                                error_msg = str(e)
                                error_type = type(e).__name__
                                if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                                    logger.warning(f"Failed to initialize Simulink MCP workbench: {e}. MCP server requires authentication or is not available. Simulink tool will not be available.")
                                else:
                                    logger.warning(f"Failed to initialize Simulink MCP workbench: {e}. Simulink tool will not be available.")
                            self._simulink_workbench = None
                
                # 如果 workbench 已初始化，尝试调用工具
                if self._simulink_workbench:
                    try:
                        # 获取可用的工具列表
                        tools: List[ToolSchema] = await self._simulink_workbench.list_tools()
                        
                        # 查找 open_simulink 工具
                        open_simulink_tool: ToolSchema | None = None
                        # logger.info(f"Tools list: {tools}")
                        for tool in tools:
                            if "open_simulink" in tool.get("name"):
                                open_simulink_tool = tool
                                break
                        
                        if open_simulink_tool:
                            # 准备工具调用参数
                            # 根据工具的参数要求，构建调用参数
                            tool_params = {
                                "command":"",
                            }
                            
                            # 调用工具打开 Simulink 软件
                            logger.info(f"Calling Simulink MCP tool: {open_simulink_tool.get('name')} with params: {tool_params}")
                            tool_call_result = await self._simulink_workbench.call_tool(
                                open_simulink_tool.get("name"),
                                tool_params,
                                cancellation_token=cancellation_token,
                            )
                            
                            # 处理工具调用结果
                            tool_result_text = tool_call_result.to_text() if hasattr(tool_call_result, 'to_text') else str(tool_call_result)
                            if tool_result_text:
                                logger.info(f"Simulink tool called successfully: {tool_result_text}")
                    except Exception as e:
                        # 如果工具调用失败，记录错误但不影响主流程
                        # 捕获网络错误、连接错误等，避免影响主流程
                        # 检查是否是 ExceptionGroup（通过检查是否有 exceptions 属性）
                        if hasattr(e, 'exceptions') and hasattr(e, '__class__') and ('ExceptionGroup' in str(type(e)) or 'BaseExceptionGroup' in str(type(e))):
                            # 处理 ExceptionGroup（Python 3.11+）
                            exceptions_list = getattr(e, 'exceptions', [])
                            for sub_exception in exceptions_list:
                                error_msg = str(sub_exception)
                                error_type = type(sub_exception).__name__
                                if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                                    logger.warning(f"Simulink MCP authentication error (non-critical): {sub_exception}. MCP server requires authentication or is not available. Skipping MCP tool call.")
                                elif "ReadError" in error_msg or "ConnectionError" in error_msg or "ConnectError" in error_msg:
                                    logger.warning(f"Simulink MCP connection error (non-critical): {sub_exception}. This may be due to network issues or MCP server not running. Skipping MCP tool call.")
                                else:
                                    logger.warning(f"Failed to call Simulink MCP tool (non-critical): {sub_exception}. Skipping MCP tool call.")
                        else:
                            # 处理普通异常
                            error_msg = str(e)
                            error_type = type(e).__name__
                            if "HTTPStatusError" in error_type or "401" in error_msg or "Unauthorized" in error_msg:
                                logger.warning(f"Simulink MCP authentication error (non-critical): {e}. MCP server requires authentication or is not available. Skipping MCP tool call.")
                            elif "ReadError" in error_msg or "ConnectionError" in error_msg or "ConnectError" in error_msg:
                                logger.warning(f"Simulink MCP connection error (non-critical): {e}. This may be due to network issues or MCP server not running. Skipping MCP tool call.")
                            else:
                                logger.warning(f"Failed to call Simulink MCP tool (non-critical): {e}. Skipping MCP tool call.")
            
            if complete:
                # 如果生成了BOM list文件，展示文件供用户下载
                bomlist_path = response.get("bomlist_path", "")
                if bomlist_path:
                    # 提取文件名（可能是相对路径或绝对路径）
                    bomlist_filename = os.path.basename(bomlist_path)
                    # 如果bomlist_path是相对路径，需要相对于工作目录
                    if not os.path.isabs(bomlist_path):
                        bomlist_relative_path = bomlist_path
                    else:
                        # 如果是绝对路径，计算相对于工作目录的相对路径
                        try:
                            bomlist_relative_path = os.path.relpath(bomlist_path, self._work_root / self._work_relative_dir)
                        except ValueError:
                            # 如果无法计算相对路径，使用文件名
                            bomlist_relative_path = bomlist_filename
                    
                    # 通知用户下载BOM list文件
                    download_info = await self.notify_to_download([bomlist_relative_path], None)
                    yield TextMessage(
                        content=download_info,
                        source=agent_name,
                        metadata={"type": "auto_download_file"},
                    )
                
                yield TextMessage(
                    content=self.RESPONSE_TEMPLATE.format(
                        message=response["message"], 
                        bomlist_path=bomlist_path),
                    source=agent_name,
                    metadata={"finished": "yes"},
                )   
                return                        
                
            # assert not isinstance(delegated_result.content, str)
        
            # ''' Temporarily using the toolcall result as the response '''
            # token_limited_context = await self._model_context.get_messages()
            # delegated_result = await self._model_client.create(
            #     token_limited_context,
            #     json_output=True
            #     if self._model_client.model_info["json_output"]
            #     else False,
            #     cancellation_token=cancellation_token
            # )  
            
            # assert isinstance(delegated_result.content, str)
            # yield TextMessage(
            #     content = delegated_result.content,
            #     source=agent_name,
            #     metadata={"finished": "yes"},
            # )   
            assert isinstance(response, dict)
            yield TextMessage(
                content=response["message"],
                source=agent_name,
                metadata={"finished": "yes", "to_user": "yes"},
            ) 
            return
        except AssertionError as e:
            logger.error(f"Assertion Error in MaterialSelectionAgent: {e}")
            raise RuntimeError(f"物料选型智能体生成物料清单失败: {e}") from e
        except Exception as e:
            logger.error(f"Error in MaterialSelectionAgent: {e}")
            raise RuntimeError(f"物料选型智能体生成物料清单失败: {e}") from e
    
    async def _get_json_response(
        self,
        messages: List[LLMMessage],
        validate_json: Callable[[Dict[str, Any]], bool],
        cancellation_token: CancellationToken,
        max_tries: int = 2,
    ) -> Dict[str, Any] | CreateResult:
        """Get a JSON response from the model client.
        Args:
            messages (List[LLMMessage]): The messages to send to the model client.
            validate_json (callable): A function to validate the JSON response. The function should return True if the JSON response is valid, otherwise False.
            cancellation_token (CancellationToken): A token to cancel the request if needed.
        """
        retries = 0
        exception_message = ""
        try:
            while retries < max_tries:
                # Re-initialize model context to meet token limit quota
                await self._model_context.clear()
                for msg in messages:
                    await self._model_context.add_message(msg)
                if exception_message != "":
                    await self._model_context.add_message(
                        UserMessage(content=exception_message, source=self._name)
                    )
                token_limited_messages = await self._model_context.get_messages()

                response = await self._model_client.create(
                    token_limited_messages,
                    json_output=True
                    if self._model_client.model_info["json_output"]
                    else False,
                    cancellation_token=cancellation_token,
                    tools=self._tools,
                )
                
                if not isinstance(response.content, str):
                    return response
                    
                try:
                    json_response = json.loads(response.content)
                    # Use the validate_json function to check the response
                    if validate_json(json_response):
                        return json_response
                    else:
                        exception_message = "JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象。"
                        logger.info(
                            f"JSON响应的验证失败: {json_response}, 正在重试 ({retries}/{max_tries})"
                        )
                except json.JSONDecodeError as e:
                    json_response = extract_json_from_string(response.content)
                    if json_response is not None:
                        if validate_json(json_response):
                            return json_response
                        else:
                            exception_message = "JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象。"
                    else:
                        exception_message = f"JSON响应的解析失败，正在重试。你必须从响应中返回一个有效JSON对象。 错误: {e}"
                    logger.info(
                        f"JSON响应的解析失败，正在重试 ({retries}/{max_tries})"
                    )
                retries += 1
            logger.info("多次尝试后，无法获得有效的JSON响应")
            raise RuntimeError(
                "多次尝试后，无法获得有效的JSON响应"
            )
        except Exception as e:
            logger.error(
                f"物料选型智能体遇到错误: {e}"
            )
            raise 
        
    def validate_output_json(self, json_response: Dict[str, Any]) -> bool:
        """Validate the JSON response."""
        if not isinstance(json_response, dict):
            return False
        required_keys = ["complete", "message", "bomlist_path"]
        for key in required_keys:
            if key not in json_response:
                return False
        return True
    
    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Clear the chat history."""
        self._chat_history.clear()

    
    async def _execute_tool_call(
        self, call: FunctionCall, cancellation_token: CancellationToken
    ) -> FunctionExecutionResult:
        # Find the tool by name.
        tool = next((tool for tool in self._tools if tool.name == call.name), None)
        assert tool is not None

        # Run the tool and capture the result.
        try:
            arguments = json.loads(call.arguments)
            result = await tool.run_json(arguments, cancellation_token)
            return FunctionExecutionResult(
                call_id=call.id, content=tool.return_value_as_string(result), is_error=False, name=tool.name
            )
        except Exception as e:
            return FunctionExecutionResult(call_id=call.id, content=str(e), is_error=True, name=tool.name)
    
    def _thread_to_context(
        self, messages: Optional[List[BaseChatMessage | BaseAgentEvent]] = None,
    ) -> List[LLMMessage]:
        """Convert the message thread to a context for the model."""
        chat_messages: List[BaseChatMessage | BaseAgentEvent] = (
            messages if messages is not None else self._chat_history
        )
        
        system_prompt = self.system_prompt_template.format(
            name=self.name,
            date_today=datetime.now().strftime("%Y-%m-%d")
        )
        context_messages: List[LLMMessage] = []
        context_messages.append(
            SystemMessage(
                content=system_prompt + "\n /no_think"
                )
        )
        if self._model_client.model_info["vision"]:
            context_messages.extend(
                thread_to_context(
                    messages=chat_messages, agent_name=self._name, is_multimodal=True
                )
            )
        else:
            context_messages.extend(
                thread_to_context(
                    messages=chat_messages, agent_name=self._name, is_multimodal=False
                )
            )

        return context_messages
    
    async def notify_to_download(
        self,
        file_and_directory_list: Annotated[List[str], "用户(客户端)可以下载的文件路径或文件夹路径的列表，可以同时包含文件路径和文件夹路径"], 
        target_directory: Annotated[str | None, "用户指定的下载存放路径，是客户端上的路径，与服务端无关。如果没有给定下载则不要指定，如果给空字符串也等价于没有指定下载路径"] = None
        ) -> str:
        r"""
        通知用户(客户端)下载file_and_directory_list列表中给定的文件和文件夹。
        target_directory是用户(客户端)上的下载保存路径，如果用户指定了则为用户指定的路径，否则为空字符串。
        此函数在FastAPI服务器端运行，用于准备文件供客户端下载。
        
        参数:
            file_and_directory_list: 需要发送给客户端的文件/文件夹路径列表
            target_directory: 客户端下载文件的目标路径（用于生成下载链接）
            
        返回:
            json: {
                "available_files": [{"name": "filename.mme", "type": "file" or "directory"}, ...],
                "nonexist_files": [{"name": "filename.mme", "type": "file" or "directory"}, ...],
                "target_directory": target_directory
            }
        """
        dict_res = await notify_to_download(str(self._work_root / self._work_relative_dir), file_and_directory_list, target_directory)
        return json.dumps(dict_res, ensure_ascii=False, indent=4)
    
    async def _generate_bom_list_file(
        self,
        specification_path: Annotated[str, '技术规格说明书文件的路径（可选，可以为空字符串）。如果为空，工具会自动在当前会话路径下查找包含"技术规格说明书"字样的.docx文档。'], 
        circuit_diagram_path: Annotated[str, "电路拓扑图文件的路径（可选，可以为空字符串）。如果为空，工具会自动在当前会话路径下查找图片文件（.jpg, .jpeg, .png, .dwg, .pdf等）。"],
        circuit_description: Annotated[str, "电路描述（可选，如果用户提供了）。"] = "",
        bomlist_filename: Annotated[str, "物料清单（BOM list）输出文件的文件名，不包括后缀"] = "物料清单",
        ) -> str:
        r"""
        根据技术规格说明书和电路拓扑图，生成物料清单（BOM list）文件。
        
        此工具会自动在当前会话路径中查找文件：
        - specification_path: 如果为空字符串，工具会在当前会话路径下查找包含"技术规格说明书"字样的.docx文档
        - circuit_diagram_path: 如果为空字符串，工具会在当前会话路径下查找图片文件（.jpg, .jpeg, .png, .dwg, .pdf等）
        
        参数:
            specification_path: 技术规格说明书文件的路径（可选）。如果为空字符串，工具会自动查找。
            circuit_diagram_path: 电路拓扑图文件的路径（可选）。如果为空字符串，工具会自动查找。
            circuit_description: 电路描述（可选）。
            bomlist_filename: 物料清单输出文件的文件名，不包括后缀
        返回:
            str: 生成结果的描述信息，包含输出文件路径
        """
        try:
            # 获取当前会话路径
            session_path = self._work_root / self._work_relative_dir
            
            # 查找技术规格说明书文件
            spec_full_path: Optional[Path] = None
            spec_path_str = specification_path.strip() if specification_path else ""
            
            # 如果提供了路径，先尝试使用提供的路径
            if spec_path_str:
                if os.sep not in spec_path_str and '/' not in spec_path_str:
                    # 如果只是文件名，直接与当前会话路径结合
                    spec_full_path = session_path / spec_path_str
                else:
                    # 如果包含路径，先尝试作为绝对路径
                    spec_full_path = Path(os.path.abspath(spec_path_str))
                    if not spec_full_path.exists():
                        # 如果绝对路径不存在，尝试与当前会话路径结合
                        spec_full_path = session_path / spec_path_str
                    if not spec_full_path.exists():
                        # 尝试使用basename
                        spec_full_path = session_path / os.path.basename(spec_path_str)
            
            # 如果未提供路径或提供的路径不存在，在当前会话路径下查找包含"技术规格说明书"字样的.docx文档
            if spec_full_path is None or not spec_full_path.exists():
                if session_path.exists():
                    for file in session_path.iterdir():
                        if file.is_file() and file.suffix.lower() == '.docx':
                            if '技术规格说明书' in file.name:
                                spec_full_path = file
                                logger.info(f"自动找到技术规格说明书文件: {spec_full_path}")
                                break
            
            if spec_full_path is None or not spec_full_path.exists():
                available_files = []
                if session_path.exists():
                    available_files = [f.name for f in session_path.iterdir() if f.is_file()]
                return f"错误：未找到技术规格说明书文件。当前会话路径: {session_path}，可用文件: {available_files}。请确保当前会话路径下有包含'技术规格说明书'字样的.docx文档。"
            
            # 查找电路拓扑图文件
            circuit_full_path: Optional[Path] = None
            circuit_path_str = circuit_diagram_path.strip() if circuit_diagram_path else ""
            
            # 如果提供了路径，先尝试使用提供的路径
            if circuit_path_str:
                if os.sep not in circuit_path_str and '/' not in circuit_path_str:
                    # 如果只是文件名，直接与当前会话路径结合
                    circuit_full_path = session_path / circuit_path_str
                else:
                    # 如果包含路径，先尝试作为绝对路径
                    circuit_full_path = Path(os.path.abspath(circuit_path_str))
                    if not circuit_full_path.exists():
                        # 如果绝对路径不存在，尝试与当前会话路径结合
                        circuit_full_path = session_path / circuit_path_str
                    if not circuit_full_path.exists():
                        # 尝试使用basename
                        circuit_full_path = session_path / os.path.basename(circuit_path_str)
            
            # 如果未提供路径或提供的路径不存在，在当前会话路径下查找图片文件
            if circuit_full_path is None or not circuit_full_path.exists():
                if session_path.exists():
                    image_extensions = ['.jpg', '.jpeg', '.png', '.dwg', '.pdf', '.bmp', '.gif', '.tiff', '.svg']
                    for file in session_path.iterdir():
                        if file.is_file() and file.suffix.lower() in image_extensions:
                            circuit_full_path = file
                            logger.info(f"自动找到电路拓扑图文件: {circuit_full_path}")
                            break
            
            if circuit_full_path is None or not circuit_full_path.exists():
                available_files = []
                if session_path.exists():
                    available_files = [f.name for f in session_path.iterdir() if f.is_file()]
                return f"错误：未找到电路拓扑图文件。当前会话路径: {session_path}，可用文件: {available_files}。请确保当前会话路径下有图片文件（.jpg, .jpeg, .png, .dwg, .pdf等）。"
            
            # 模拟生成物料清单文件（实际实现中会调用真实的物料选型算法）
            bomlist_path = self._work_root / self._work_relative_dir / (bomlist_filename + ".json")
            
            # 创建模拟的物料清单数据
            mock_bom_data = {
                "bom_info": {
                    "generated_from_specification": specification_path,
                    "generated_from_circuit": circuit_diagram_path,
                    "output_file": str(bomlist_path),
                    "generation_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
                "materials": [
                    {
                        "material_id": "MAT001",
                        "name": "IGBT模块",
                        "specification": "1200V/300A",
                        "quantity": 4,
                        "manufacturer": "示例厂商A",
                        "part_number": "IGBT-1200-300",
                        "parameters": {
                            "voltage_rating": "1200V",
                            "current_rating": "300A",
                            "power_rating": "360kW"
                        }
                    },
                    {
                        "material_id": "MAT002",
                        "name": "滤波电容",
                        "specification": "1000uF/450V",
                        "quantity": 6,
                        "manufacturer": "示例厂商B",
                        "part_number": "CAP-1000-450",
                        "parameters": {
                            "capacitance": "1000uF",
                            "voltage_rating": "450V",
                            "type": "电解电容"
                        }
                    },
                    {
                        "material_id": "MAT003",
                        "name": "驱动模块",
                        "specification": "15V/2A",
                        "quantity": 4,
                        "manufacturer": "示例厂商C",
                        "part_number": "DRV-15-2",
                        "parameters": {
                            "voltage": "15V",
                            "current": "2A",
                            "isolation_voltage": "2500V"
                        }
                    }
                ],
                "summary": {
                    "total_materials": 3,
                    "total_components": 14,
                    "total_cost_estimate": "待评估"
                }
            }
            
            # 保存模拟的物料清单文件
            with open(bomlist_path, "w", encoding="utf-8") as f:
                json.dump(mock_bom_data, f, ensure_ascii=False, indent=2)
            
            bom_info = cast(Dict[str, Any], mock_bom_data['bom_info'])
            summary = cast(Dict[str, Any], mock_bom_data['summary'])
            
            # 复制实际的BOM清单文件
            actual_bomlist_filename = "1_上海机场线牵引变流器-早期BOM清单-20220114.xls"
            actual_bomlist_path = self._work_root / self._work_relative_dir / actual_bomlist_filename
            cwd = os.getcwd()
            source_file = os.path.join(cwd, "misc/1_上海机场线牵引变流器-早期BOM清单-20220114.xls")
            if os.path.exists(source_file):
                shutil.copy(source_file, actual_bomlist_path)
                # 使用实际保存的文件路径（相对路径，用于返回给LLM）
                bomlist_relative_path = actual_bomlist_filename
            else:
                # 如果源文件不存在，使用之前生成的JSON文件路径（相对路径）
                bomlist_relative_path = os.path.relpath(bomlist_path, self._work_root / self._work_relative_dir)

            # 使用实际找到的文件路径
            actual_spec_path = str(spec_full_path.name) if spec_full_path else specification_path
            actual_circuit_path = str(circuit_full_path.name) if circuit_full_path else circuit_diagram_path
            
            return f"""
物料清单（BOM list）已生成，保存在 \"{bomlist_relative_path}\" 文件中。

物料清单信息：
- 基于技术规格说明书: {actual_spec_path}
- 基于电路拓扑图: {actual_circuit_path}
- 输出文件: {bomlist_relative_path}
- 生成时间: {bom_info.get('generation_time', 'N/A')}
- 物料种类数: {summary.get('total_materials', 0)}
- 总组件数量: {summary.get('total_components', 0)}
- 成本估算: {summary.get('total_cost_estimate', 'N/A')}

物料参数列表：
{json.dumps(mock_bom_data['materials'], ensure_ascii=False, indent=2)}
"""
            
        except Exception as e:
            logger.error(f"生成物料清单失败: {e}")
            return f"生成物料清单失败: {str(e)}"

    async def _read_file(self, 
        file_path: Annotated[str, "文件的路径或名字"], 
    ):
        """
        读取文件，并返回文件内容。只有UTF-8编码的文件才会返回内容。
        如果文件不是UTF-8编码，则返回错误信息。
        
        如果只提供了文件名（不包含路径分隔符），会在当前会话路径中查找文件。
        """
        # 获取当前会话路径
        session_path = self._work_root / self._work_relative_dir
        
        # 判断是否是纯文件名（不包含路径分隔符）
        file_path_str = file_path.strip()
        if os.sep not in file_path_str and '/' not in file_path_str:
            # 如果只是文件名，直接与当前会话路径结合
            full_file_path = session_path / file_path_str
        else:
            # 如果包含路径，先尝试作为绝对路径
            full_file_path = Path(os.path.abspath(file_path_str))
            if not full_file_path.exists():
                # 如果绝对路径不存在，尝试与当前会话路径结合
                full_file_path = session_path / file_path_str
        
        # 如果文件不存在，尝试在当前会话路径中查找文件名
        if not full_file_path.exists():
            full_file_path = session_path / os.path.basename(file_path_str)
        
        if not full_file_path.exists():
            return f"错误：文件 '{file_path}' 不存在。尝试的路径包括：{full_file_path}"
        
        try:
            content = await read_file(str(full_file_path))
            return content
        except Exception as e:
            return f"错误：读取文件 '{file_path}' 时发生异常：{str(e)}"

    def _to_config(self) -> MaterialSelectionAgentConfig:
        """Convert the agent's state to a configuration object."""
        return MaterialSelectionAgentConfig(
            name=self.name,
            run_id=self._run_id,
            model_client=self._model_client.dump_component(),
            work_root=str(self._work_root),
            work_relative_dir=str(self._work_relative_dir),
            bind_root=str(self._bind_root),
            bind_relative_dir=str(self._bind_relative_dir),
            description=self.description,
            max_reties=self._max_reties,
            summarize_output=self._summarize_output,
            client_ip=self._client_ip,
            # TODO: Optionally add code_executor configuration if supported
        )
        
    @classmethod
    def _from_config(cls, config: MaterialSelectionAgentConfig) -> Self:
        """Create an agent instance from a configuration object."""
        return cls(
            name=config.name,
            run_id=config.run_id,
            model_client=ChatCompletionClient.load_component(config.model_client),
            work_root=Path(config.work_root),
            work_relative_dir=Path(config.work_relative_dir),
            bind_root=Path(config.bind_root),
            bind_relative_dir=Path(config.bind_relative_dir),
            description=config.description,
            max_reties=config.max_reties,
            summarize_output=config.summarize_output,
            client_ip=config.client_ip,
            # TODO: Optionally load code_executor from config if provided
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
