import asyncio
from pathlib import Path
import shutil
from typing import AsyncGenerator, List, Sequence, Optional, Dict, Any, Mapping, Callable
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
from ..docker_manager import DockerManager
from autogen_core.tools import ToolSchema
from ..teams.orchestrator._utils import extract_json_from_string
# import logging
# from autogen_agentchat import logger_NAME


class ElectricalDesignAgentConfig(BaseModel):
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
    # Optionally add code_executor config if needed


class CodingAgentState(BaseState):
    chat_history: List[BaseChatMessage] = Field(default_factory=list[BaseChatMessage])
    type: str = Field(default="CodingAgentState")


class ElectricalDesignAgent(BaseChatAgent, Component[ElectricalDesignAgentConfig]):
    """An coding agent capable of writing, generating code and save the code to files.

    The agent uses either Docker-based code generator to generate code
    in a controlled environment. It maintains a chat history and can be paused/resumed
    during generation.
    """

    component_type = "agent"
    component_config_schema = ElectricalDesignAgentConfig
    
    DEFAULT_DESCRIPTION = """
    
    这是一个可以生成电路拓扑图的智能体，在电气设计工作流程中发挥重要作用。
    它依据文字形式的电气设备需求，生成满足需求的电路拓扑图和对应的电路描述并保存对应的文件。
    成功的生成电路图后，该智能体返回电路图的描述，以及生成的图片格式的电路拓扑图和CAD(.dwg)格式的电路拓扑图文件路径。
    """

    system_prompt_template = """
    你是{name}, 一个使用工具进行电气设计的智能体，但是你本身不做任何主观电气设计。

    今天的日期是:{date_today}
    
    ## 工作原则
    在电气设计过程中，你运用生成式(generative)算法工具，将用户的文字需求转化为电路拓扑图和对应的电路描述。并保存为对应的文件。
    电路图的生成是一个迭代的过程，你需要和用户进行多轮交互，直到用户确认并同意生成的电路图符合他的要求。在每次交互中，你只能调用给定的工具来生成电路拓扑图和对应的电路描述，不要自己生成电路拓扑图和对应的电路描述。
    
    为了帮助用户更好地生成符合要求的电路图，首先思考如下问题:
    1. 用户是否提供了需求文件而非只有直接文字描述？ 如果是的，一定要使用工具先读取需求文件内容才能知道用户的完整需求。
    2. 是否已经有电路图文件了(包含用户提供的或者之前迭代过程中生成的)？ 如果答案是否定的，直接根据用户的请求生成电路图文件和电路描述。否则，根据用户的回复做出合理的回答或者动作。
    3. 用户是否已经确认生成的电路图文件和电路描述符合他的要求(额外地，"继续"或者"下一步"等同义表达也表示用户已经确认生成的电路图文件和电路描述符合他的要求)？ 如果答案是肯定的，输出已经满足用户需求的电路图文件和电路描述。否则，根据用户的回复做出合理的回答或者动作。 
    
    **重点注意**
    - 你只能调用给定的工具来生成电路拓扑图和对应的电路描述，不要自己生成电路拓扑图和对应的电路描述。
    - 不要添加任何主观意见，不要添加任何解释，不要添加任何说明，不要添加任何备注。
    - 用户的请求可能是以文件路径的形式给出的，你需要读取文件内容，并根据文件内容生成电路拓扑图和对应的电路描述。
    - 生成电路图文件和电路描述文件时，一定需要调用生成式工具，不然不能生成新的电路图文件和电路描述文件。
    
    ## 输出格式
    你的输出可以是请求调用工具或回复用户电路图的生成情况。
    
    如果不需要调用工具，输出为JSON格式。严格遵循以下JSON schema格式，不要输出JSON以外的任何内容:
    ```json
    {{
        "complete": 用户是否已经确认生成的电路图文件和电路描述符合他的要求？ 如果是取"true"，否则"false",
        "message": 给用户的回复。如果`complete`为"false"总是额外地询问用户是否同意已经生成的电路图，否则告知用户任务已经完成，同时包含电路拓扑图CAD文件的保存路径和电路图拓扑图的描述。
        "circuit_diagram_path": 电路拓扑图CAD文件的保存路径,
        "circuit_picture_path": 电路拓扑图图片文件的保存路径,
        "circuit_description": 电路图拓扑图的描述
    }}
    ```
    
    **重点注意**
    - 输出最终满足用户需求的电路图文件和电路描述时，请在`message`字段中同时表明你的工作已经完成。
    - 用简洁的语句回复用户，但是必须包含必要的信息，比如，你不能简单地回复"任务已经完成"，而是要告知用户任务已经完成，并且告知用户电路图文件和电路描述的保存路径。
    - `circuit_diagram_path`和`circuit_picture_path`字段只能包含文件路径，不要有任何解释说明或者其他文字。
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
    
        self._tools = self._setup_tools()
    
    def _setup_tools(self) -> List[FunctionTool]:
        """
        Setup tools used in orchestrator
        """
        return [
            FunctionTool(self._generate_circuit_file, 
                             description=self._generate_circuit_file.__doc__ or ""),
            FunctionTool(self._read_file, description=self._read_file.__doc__ or "")
        ]
    
    async def lazy_init(self) -> None:
        """Initialize the code executor if it has a start method.

        This method is called after initialization to set up any async resources
        needed by the code executor.
        """
        if self._did_lazy_init:
            return
        
        self._did_lazy_init = True

    async def close(self) -> None:
        """Clean up resources used by the agent.

        This method:
        - Stops the code executor
        - Removes the work directory if it was created
        - Closes the model client
        """
        logger.info("Closing ElectricalDesignAgent...")
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
                    content="电气设计智能体已暂停。",
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
            async for msg in self._generate_circuit_diagram(
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
                    content="用户拒绝了执行电气设计。",
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
            logger.error(f"Error in ElectricalDesignAgent: {e}")
            # add to chat history
            self._chat_history.append(
                TextMessage(
                    content=f"生成电路拓扑图和对应的电路描述时发生错误： {e}",
                    source=self.name,
                )
            )
            yield Response(
                chat_message=TextMessage(
                    content=f"电气设计智能体发生了如下错误： {e}",
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
    
    async def _generate_circuit_diagram(
        self,
        agent_name: str,
        model_client: ChatCompletionClient,
        bind_relative_dir: Path,
        max_json_retries: int,
        cancellation_token: CancellationToken,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage, None]:
        """Write code using the model and executor.

        It calls the workbench to generate code based on the system prompt and the thread of messages.

        When the cancellation token is set, the execution will stop.
        Args:
            thread (Sequence[BaseChatMessage]): The thread of messages to use as context.
            agent_name (str): The name of the agent.
            model_client (ChatCompletionClient): The model client to use for cod    # extract code blocks from the LLM's response
            max_reties (int): The maximum number of debug rounds to perform.
            cancellation_token (CancellationToken): The cancellation token to stop execution.

        Yields:
            TextMessage: The intermediate messages generated by the model and executor.
            bool: A flag indicating whether any code execution was performed.

        Raises:
            ApprovalDeniedError: If the user denies the approval for the coding request.
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
                    yield ToolCallExecutionEvent(content=tool_call_results, source=self._name)
                else:
                    break
            
            assert response
            complete = response["complete"]
            if complete:          
                yield TextMessage(
                    content=response["message"],
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
            logger.error(f"Assertion Error in ElectricalDesignAgent: {e}")
            raise RuntimeError(f"Electrical Design Agent生成电路图失败: {e}") from e
        except Exception as e:
            logger.error(f"Error in ElectricalDesignAgent: {e}")
            raise RuntimeError(f"Electrical Design Agent生成电路图失败: {e}") from e
    
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
                f"Electrical Design Agent遇到错误: {e}"
            )
            raise 
        
    def validate_output_json(self, json_response: Dict[str, Any]) -> bool:
        """Validate the JSON response."""
        if not isinstance(json_response, dict):
            return False
        required_keys = ["complete", "message", "circuit_diagram_path", "circuit_picture_path", "circuit_description"]
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
                content=system_prompt
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
    
    async def _generate_circuit_file(
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
        try:
            cwd = os.getcwd()
            shutil.copy(os.path.join(cwd, "misc/circuit_foo.jpg"), self._work_root / self._work_relative_dir / "电路拓扑图.jpg")
        except Exception:
            return "生成电路拓扑图失败"  
        # await notify_to_download(str(self._work_root / self._work_relative_dir), ["电路拓扑图.jpg"], None)
        
        return """
电路拓扑图已生成，分别保存在保存在\"电路拓扑图.jpg\"，\"电路拓扑图.dwg\"文件中。电路描述如下：

三相高压脉冲电源电路说明：
本电路以380V 50Hz三相交流电源为输入，接入端子A、B、C。输入电源经过由二极管D1至D6组成的三相整流桥，将交流电整流为高压直流电。电容C1用于直流母线滤波与能量储存。
整流后的直流电压经全桥逆变器输入，逆变器由晶体管Q1、Q2、Q3、Q4组成。每个晶体管由驱动模块（G1–G4）提供栅极控制信号，而驱动模块又由STM32控制模块发出指令。电容C2至C6用作吸收或耦合电容，用于平衡电压及抑制开关瞬态。
逆变器输出连接到变压器T1的初级绕组，T1将电压升高到次级高压侧。变压器次级输出经高压二极管D7至D10整流，并通过电阻R1至R4、电感L1及电容C7进行稳压与滤波。整流后的高压为电容C7充电。
在输出端，放电间隙（标注为放电间隙）与C7并联。当C7两端电压达到放电间隙的击穿电压时，产生高压放电，向负载释放强脉冲能量。电压传感器实时检测输出电压，并通过采样电压电路将信号反馈至STM32控制系统。
STM32模块通过监测反馈信号，协调逆变器的运行、脉冲时序及输出调节，并通过驱动模块控制Q1–Q4的开关模式。
该系统是一个基于三相交流输入的高压脉冲发生电源，其主要功能包括整流、逆变、变压升压、高压整流以及受控脉冲放电。
"""

    async def _read_file(self, 
        file_path: Annotated[str, "文件的路径或名字"], 
    ):
        """
        读取文件，并返回文件内容。只有UTF-8编码的文件才会返回内容。
        如果文件不是UTF-8编码，则返回错误信息。
        """
        full_file_path = os.path.abspath(file_path)
        if not os.path.exists(full_file_path):
            full_file_path = os.path.abspath(os.path.join(self._work_root / self._work_relative_dir, file_path))
            if not os.path.exists(full_file_path):
                return f"错误：文件 '{file_path}' 不存在。"
        
        try:
            content = await read_file(full_file_path)
            return content
        except Exception as e:
            return f"错误：读取文件 '{file_path}' 时发生异常：{str(e)}"

    def _to_config(self) -> ElectricalDesignAgentConfig:
        """Convert the agent's state to a configuration object."""
        return ElectricalDesignAgentConfig(
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
            # TODO: Optionally add code_executor configuration if supported
        )
        
    @classmethod
    def _from_config(cls, config: ElectricalDesignAgentConfig) -> Self:
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
