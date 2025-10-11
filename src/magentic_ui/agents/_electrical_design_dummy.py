import asyncio
from pathlib import Path
import shutil
from typing import AsyncGenerator, List, Sequence, Optional, Dict, Any, Mapping
from typing_extensions import Annotated
import json, os
from autogen_core.tools import Workbench
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
import httpx
import uuid

from autogen_agentchat.agents import BaseChatAgent
from autogen_core.model_context import (
    ChatCompletionContext,
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
    FunctionExecutionResult,
    AssistantMessage,
    FunctionExecutionResultMessage,
)

from autogen_core.tools import FunctionTool

from magentic_ui.tools.playwright.browser.utils import get_available_port
from ._utils import notify_to_download
from ..utils import thread_to_context

from ..approval_guard import BaseApprovalGuard
from ..guarded_action import ApprovalDeniedError
from ..tools.mcp import AggregateMcpWorkbench
from ..docker_manager import DockerManager
from autogen_core.tools import ToolSchema
from ..teams.orchestrator._utils import extract_json_from_string
# import logging
# from autogen_agentchat import logger_NAME


class CodingDelegatorAgentConfig(BaseModel):
    name: str
    run_id: int
    model_client: ComponentModel
    description: str = """
    """
    max_reties: int = 3
    summarize_output: bool = False
    coding_provider: str
    work_root: str
    work_relative_dir: str
    bind_root: str
    bind_relative_dir: str
    # Optionally add code_executor config if needed


class CodingAgentState(BaseState):
    chat_history: List[BaseChatMessage] = Field(default_factory=list[BaseChatMessage])
    type: str = Field(default="CodingAgentState")


class CodingDelegatorAgent(BaseChatAgent, Component[CodingDelegatorAgentConfig]):
    """An coding agent capable of writing, generating code and save the code to files.

    The agent uses either Docker-based code generator to generate code
    in a controlled environment. It maintains a chat history and can be paused/resumed
    during generation.
    """

    component_type = "agent"
    component_config_schema = CodingDelegatorAgentConfig
    component_provider_override = "magentic_ui.agents.CodingAgent"
    
    DEFAULT_DESCRIPTION = """
    这是一个电气设计智能体，在电气设计工作流程中发挥重要作用。它依据文字形式的电气设备需求，生成满足需求的电路拓扑图和对应的电路描述。
    """

    system_prompt_coding_agent_template = """
    你是{name}, 一个电气设计智能体，你专业于生成电路拓扑图和对应的电路描述。

    今天的日期是:{date_today}
    
    ## 目的
    在电气设计过程中，你运用生成式算法工具，将用户的文字需求转化为电路拓扑图和对应的电路描述。并保存为对应的文件。
    """

    def __init__(
        self,
        name: str,
        run_id: int,
        model_client: ChatCompletionClient,
        coding_provider: str,
        work_root: Path,
        work_relative_dir: Path,
        bind_root: Path,
        bind_relative_dir: Path,
        code_manager: Optional[DockerManager] = None,
        model_context_token_limit: int = 128000,
        description: str = DEFAULT_DESCRIPTION,
        max_reties: int = 2,
        summarize_output: bool = False,
        approval_guard: BaseApprovalGuard | None = None,
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
        self._coding_provider = coding_provider
        self._code_manager = code_manager
        self._coding_workbench = None
    
        self._tools = self._setup_tools()
    
    def _setup_tools(self) -> List[FunctionTool]:
        """
        Setup tools used in orchestrator
        """
        return [FunctionTool(self.notify_to_download, 
                             description=self.notify_to_download.__doc__ or "")]
    
    async def lazy_init(self) -> None:
        """Initialize the code executor if it has a start method.

        This method is called after initialization to set up any async resources
        needed by the code executor.
        """
        if self._did_lazy_init:
            return
        
        if not self._code_manager:
            from .._docker import CODING_IMAGE
            from ..tools.mcp import NamedMcpServerParams
            from autogen_ext.tools.mcp import SseServerParams
            
            assert os.environ["CODING_WORKSPACE"] and \
                os.environ["CODING_WORKSPACE_IN_DOCKER"]
        
            """Initialize the docker manager"""
            # get a available port for the gemini_mcp server
            port, socket = get_available_port()
            socket.close()
            
            coding_tool = NamedMcpServerParams(server_name="gemini_cli", 
                                                server_params=SseServerParams(url=f"http://localhost:{str(port)}/sse"))
            assert self._coding_workbench is None
            self._coding_workbench = AggregateMcpWorkbench(named_server_params=[coding_tool])
            container_name = "gemini_mcp-" + str(self._run_id) + "-" + str(port) + "-" + str(uuid.uuid4())
            self._code_manager = DockerManager(
                image=CODING_IMAGE,
                container_name=container_name,
                success_log_pattern="Application startup complete.*Uvicorn running on",
                working_dir=os.environ["CODING_WORKSPACE_IN_DOCKER"],
                volumes={
                    os.environ["CODING_WORKSPACE"]: {"bind": os.environ["CODING_WORKSPACE_IN_DOCKER"], "mode": "rw"},
                    str(self._work_root / self._work_relative_dir): {"bind": str(self._bind_root / self._bind_relative_dir), "mode": "rw"},
                    },
                ports={"18100/tcp": str(port)}, # docker port is 18100/tcp, local port is the latter
                delete_tmp_files=True,
                init_command="bash -c 'source /data/gemini-cli/run.sh'",
                detach=True,
                tty=True,
                auto_remove=True,
                stop_container=True,
            )
        
        # lx-todo, makr the start status of the code manager (docker container)
        if self._code_manager:
            await self._code_manager.start()
                
        self._did_lazy_init = True

    async def close(self) -> None:
        """Clean up resources used by the agent.

        This method:
        - Stops the code executor
        - Removes the work directory if it was created
        - Closes the model client
        """
        logger.info("Closing CodingDelegatorAgent...")
        # self._code_manager will not be None
        await self._code_manager.stop() # type: ignore
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
                    content="代码助手已暂停。",
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

        system_prompt_coding_agent = self.system_prompt_coding_agent_template.format(
            name=self.name,
            date_today=datetime.now().strftime("%Y-%m-%d")
        )

        try:
            # Run the code execution and debugging process.
            async for msg in self._coding(
                system_prompt=system_prompt_coding_agent,
                inner_messages=inner_messages,
                thread=self._chat_history,
                agent_name=self.name,
                coding_provider=self._coding_provider,
                bind_relative_dir=self._bind_relative_dir,
                model_client=self._model_client,
                workbench=self._coding_workbench, # type: ignore
                max_json_retries=self._max_reties,
                cancellation_token=code_execution_token,
                model_context=self._model_context,
            ):
            # Display some messages to the UI by setting event.metadata = {"internal": False}
                
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
                    metadata = {
                        **metadata,
                        # Display in UI
                        "internal": "no",
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
                    content="用户拒绝了执行代码。",
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
            logger.error(f"Error in CodingDelegatorAgent: {e}")
            # add to chat history
            self._chat_history.append(
                TextMessage(
                    content=f"生成代码时发生错误： {e}",
                    source=self.name,
                )
            )
            yield Response(
                chat_message=TextMessage(
                    content=f"coding智能体生成代码时发生如下错误： {e}",
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
    
    async def _coding(
        self,
        system_prompt: str,
        inner_messages: List[BaseAgentEvent | BaseChatMessage],
        thread: Sequence[BaseChatMessage | BaseAgentEvent],
        agent_name: str,
        model_client: ChatCompletionClient,
        workbench: Workbench,
        coding_provider: str,
        bind_relative_dir: Path,
        max_json_retries: int,
        cancellation_token: CancellationToken,
        model_context: ChatCompletionContext,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage, None]:
        """Write code using the model and executor.

        It calls the workbench to generate code based on the system prompt and the thread of messages.

        When the cancellation token is set, the execution will stop.
        Args:
            system_prompt (str): The system prompt to guide the model.
            thread (Sequence[BaseChatMessage]): The thread of messages to use as context.
            agent_name (str): The name of the agent.
            model_client (ChatCompletionClient): The model client to use for cod    # extract code blocks from the LLM's response
            workbench (Workbench): The workbench to use for generating code.
            max_reties (int): The maximum number of debug rounds to perform.
            cancellation_token (CancellationToken): The cancellation token to stop execution.
            model_context (ChatCompletionContext): The context for the model.

        Yields:
            TextMessage: The intermediate messages generated by the model and executor.
            bool: A flag indicating whether any code execution was performed.

        Raises:
            ApprovalDeniedError: If the user denies the approval for the coding request.
        """
        # The list of new messages to be added to the thread.
        # Add system prompt as the last message before generation
        current_thread = (
            list(thread)
        )
        context = thread_to_context(
            current_thread,
            agent_name,
            is_multimodal=model_client.model_info["vision"],
        )
        # the delegator only take as input the last message to analyze
        # the historical messages are ignored
        last_message = context[-1]
        delegator_context = [SystemMessage(content=system_prompt), last_message]

        # Re-initialize model context to meet token limit quota
        try:
            await model_context.clear()
            for msg in delegator_context:
                await model_context.add_message(msg)
            token_limited_context = await model_context.get_messages()
        except Exception:
            token_limited_context = delegator_context
        
        # check the mcp tools match the coding_provider
        try:
            tools: List[ToolSchema] = await workbench.list_tools()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error when listing MCP tools: {e}")
            raise Exception("获取代码MCP工具失败，http错误") from e
        except Exception as e:
            logger.error(f"Unexpected error when listing MCP tools: {e}")
            raise Exception("获取代码MCP工具失败，遇到未知错误，无法修复") from e
            
        coding_tool: ToolSchema | None = None
        # find the expeceted coding_tool
        
        for tool in tools:
            if self.validate_tool_parameters(tool):
                coding_tool = tool
                break 
        else:
            raise ValueError(f"未找到代码工具{coding_provider}，无法提供代码能力")
        
        # preprocess the user request and extract coding tool parameters
        retries = 0
        delegated_json_response = None
        exception_message = ""
        try:
            while retries < max_json_retries:
                if exception_message:
                    await model_context.add_message(
                        UserMessage(content=exception_message, source=agent_name)
                    )
                token_limited_context = await model_context.get_messages()
                delegated_result = await model_client.create(
                    token_limited_context,
                    json_output=True
                    if model_client.model_info["json_output"]
                    else False,
                    cancellation_token=cancellation_token,
                )
                
                '''
                # List[FunctionCall]
                if not isinstance(delegated_result.content, str):
                    await self._model_context.add_message(AssistantMessage(content=delegated_result.content, source=self._name))
                    tool_call_results = await asyncio.gather(*[self._execute_tool_call(function_call, cancellation_token) for function_call in delegated_result.content])
                    
                    delegated_result = await self._model_client.create(
                        token_limited_context,
                        json_output=True
                        if self._model_client.model_info["json_output"]
                        else False,
                        cancellation_token=cancellation_token,
                        tools=self._tools
                    )  
                    
                    await self._model_context.add_message(FunctionExecutionResultMessage(content=tool_call_results))
                    tool_call_result_text = tool_call_results[0].content
                    # send the download notification to the client, the type "auto_download_file" is used to identify the download notification
                    if not tool_call_results[0].is_error:
                        yield TextMessage(
                            content = tool_call_result_text,
                            source=agent_name,
                            metadata={"type": "auto_download_file"},
                        )   
                    
                    delegated_result = await self._model_client.create(
                        token_limited_context,
                        json_output=True
                        if self._model_client.model_info["json_output"]
                        else False,
                        cancellation_token=cancellation_token
                    )  
                    
                    assert isinstance(delegated_result.content, str)
                    yield TextMessage(
                        content = delegated_result.content,
                        source=agent_name,
                        metadata={"finished": "yes"},
                    )   
                    return
                ''' 
                
                assert isinstance(delegated_result.content, str)
                try:
                    logger.debug(f"Coding Delegator Agent: {delegated_result.content}")
                    delegated_json_response = json.loads(delegated_result.content)
                    # Use the validate_json function to check the response
                    if self.validate_output_json(delegated_json_response):
                        break
                    else:
                        exception_message = "JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象。"
                        logger.debug(
                            f"JSON响应的验证失败: {delegated_json_response}, 正在重试 ({retries}/{max_json_retries})"
                        )
                except json.JSONDecodeError as e:
                    #lx-todo, sometimes the delegated_result.content is a plausible but the json.loads failed and report 'Expecting value: line 1 column 1 (char 0)'
                    delegated_json_response = extract_json_from_string(delegated_result.content)
                    if delegated_json_response is not None:
                        if self.validate_output_json(delegated_json_response):
                            break
                        else:
                            exception_message = "JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象。"
                    else:
                        logger.error(f"Failed to parse JSON response, retrying. {e}, {delegated_result.content}")
                        exception_message = f"JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象。错误: {e}"
                    logger.debug(
                        f"Failed to parse JSON response, retrying ({retries}/{max_json_retries})"
                    )
                retries += 1
            else:
                raise ValueError(f"JSON响应的验证失败，{max_json_retries}尝试后仍然没有得到有效的JSON响应")
        except Exception as e:
            logger.error(f"Error in CodingDelegatorAgent: {e}")
            raise
        
        assert delegated_json_response is not None
        # call the coding tool
        
        # currently, not allowing customized generating path
        delegated_json_response["save_path"] = str(bind_relative_dir)
        # delegated_json_response will not be appended to the chat_history

        delegated_json_response["request"] = "上下文和历史对话消息:\n" + "\n".join(i.content for i in context if isinstance(i.content, str)) + "\n 当前输入:\n" + delegated_json_response["request"]
        try:
            tool_call_result = await workbench.call_tool(
                coding_tool.get("name"),
                delegated_json_response,
                cancellation_token=cancellation_token,
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error when calling MCP tool: {e}")
            raise Exception("调用代码MCP工具失败，http错误") from e
        
        except Exception as e:
            logger.error(f"Unexpected error when calling MCP tool: {e}")
            raise Exception("调用代码MCP工具失败，遇到未知错误，无法修复") from e
        
        ''' Temporarily, always find all generated codes and notify the user to download '''
        work_path = self._work_root / self._work_relative_dir
        assert work_path.exists()
        file_list: List[str] = []
        for root, dirs, files in os.walk(work_path, topdown=False):
            for file in files:
                file_path = Path(root) / file
                relative_path = file_path.relative_to(work_path)
                file_list.append(str(relative_path))
            for dir_name in dirs:
                dir_path = Path(root) / dir_name
                relative_path = dir_path.relative_to(work_path)
                file_list.append(str(relative_path))
        
        if file_list:
            logger.debug("Notify to download")
            download_file_list = await self.notify_to_download(file_list, None)
            yield TextMessage(
                    content=download_file_list,
                    source=agent_name,
                    metadata={"type": "auto_download_file"},
                )  
        
        tool_call_result_text = tool_call_result.to_text()
        yield TextMessage(
            content = tool_call_result_text if tool_call_result_text else "调用代码工具没有返回结果，出现错误，代码工具无法使用",
            source=agent_name,
            metadata={"finished": "yes"},
        )   
    
    def validate_tool_parameters(self, tool: ToolSchema) -> bool:
        """Validate the tool parameters."""
        if not tool.get("parameters", {}):
            raise ValueError(f"Coding tool {tool.get('name')} does not accept any parameters")
        tool_parameters: Dict[str, Any] = tool.get("parameters", {}).get("properties", {})
        required_parameters = ("request", "save_path")
        for parameter in required_parameters:
            if parameter not in tool_parameters:
                raise ValueError(f"Coding tool {tool.get('name')} does not accept the parameter {parameter}")

        return True
        
    def validate_output_json(self, json_response: Dict[str, Any]) -> bool:
        """Validate the JSON response."""
        if not isinstance(json_response, dict):
            return False
        required_keys = ["request", "save_path"]
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
        
    async def notify_to_download(
        self,
        file_and_directory_list: Annotated[List[str], "用户(客户端)可以下载的文件路径或文件夹路径的列表，可以同时包含文件路径和文件夹路径"], 
        target_directory: Annotated[str | None, "用户指定的下载存放路径，是客户端上的路径，与服务端无关。如果没有给定下载则不要指定，如果给空字符串也等价于没有指定下载路径"]
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
    
    async def generate_circuit_diagram(
        self,
        circuit_requirments: Annotated[str, "用户(客户端)可以下载的文件路径或文件夹路径的列表，可以同时包含文件路径和文件夹路径"], 
        ) -> str:
        r"""
        根据circuit_requirments的描述，生成电路拓扑图和对应的电路描述。
        
        参数:
            circuit_requirments: 电路需求描述
            
        返回:
            json: {
                "available_files": [{"name": "filename.mme", "type": "file" or "directory"}, ...],
                "nonexist_files": [{"name": "filename.mme", "type": "file" or "directory"}, ...],
                "target_directory": target_directory
            }
        """
        shutil.copy("circuit_diagram.png", self._work_root / self._work_relative_dir / "circuit_diagram.png")
        
        return "电路拓扑图和对应的电路描述已生成"

    def _to_config(self) -> CodingDelegatorAgentConfig:
        """Convert the agent's state to a configuration object."""
        return CodingDelegatorAgentConfig(
            name=self.name,
            run_id=self._run_id,
            model_client=self._model_client.dump_component(),
            work_root=str(self._work_root),
            work_relative_dir=str(self._work_relative_dir),
            bind_root=str(self._bind_root),
            bind_relative_dir=str(self._bind_relative_dir),
            coding_provider=self._coding_provider,
            description=self.description,
            max_reties=self._max_reties,
            summarize_output=self._summarize_output,
            # TODO: Optionally add code_executor configuration if supported
        )
        
    @classmethod
    def _from_config(cls, config: CodingDelegatorAgentConfig) -> Self:
        """Create an agent instance from a configuration object."""
        return cls(
            name=config.name,
            run_id=config.run_id,
            model_client=ChatCompletionClient.load_component(config.model_client),
            work_root=Path(config.work_root),
            work_relative_dir=Path(config.work_relative_dir),
            bind_root=Path(config.bind_root),
            bind_relative_dir=Path(config.bind_relative_dir),
            coding_provider=config.coding_provider,
            description=config.description,
            max_reties=config.max_reties,
            summarize_output=config.summarize_output,
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
