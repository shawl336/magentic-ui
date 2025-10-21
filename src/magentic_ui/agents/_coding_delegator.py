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
from ..guarded_action import ApprovalDeniedError, TrivialGuardedAction
from ..tools.mcp import AggregateMcpWorkbench, NamedMcpServerParams
from ..docker_manager import DockerManager
from autogen_core.tools import ToolSchema
from ..teams.orchestrator._utils import extract_json_from_string
# import logging
# from autogen_agentchat import logger_NAME


r'''
def _extract_markdown_code_blocks(markdown_text: str) -> List[CodeBlock]:
    pattern = re.compile(r"```(?:\s*([\w\+\-]+))?\n([\s\S]*?)```")
    matches = pattern.findall(markdown_text)
    code_blocks: List[CodeBlock] = []
    for match in matches:
        language = match[0].strip() if match[0] else ""
        code_content = match[1]
        code_blocks.append(CodeBlock(code=code_content, language=language))
    return code_blocks


async def _invoke_action_guard(
    thread: Sequence[BaseChatMessage | BaseAgentEvent],
    delta: Sequence[BaseChatMessage | BaseAgentEvent],
    code_message: TextMessage,
    agent_name: str,
    model_client: ChatCompletionClient,
    approval_guard: BaseApprovalGuard | None,
) -> None:
    # Get approval for the coding request. We could conceivably do extra work to enable interactive approval here,
    # but the value for many users is likely to be low, as it may not be appropriate to assume knowledge of coding,
    # and thus the user will not have the context necessary to approve/deny the incremental execution of code blocks.
    guarded_action = TrivialGuardedAction("coding", baseline_override="maybe")

    # Note that delta already contains the code message.
    assert delta[-1] == code_message

    thread = list(thread) + list(delta)

    context = thread_to_context(
        thread,
        agent_name,
        is_multimodal=model_client.model_info["vision"],
    )
    action_description_for_user = TextMessage(
        content="Do you want to execute the code above?",
        source=agent_name,
    )

    await guarded_action.invoke_with_approval(
        {}, code_message, context, approval_guard, action_description_for_user
    )
'''

'''
async def _summarize_coding(
    agent_name: str,
    model_client: ChatCompletionClient,
    thread: Sequence[BaseChatMessage | BaseAgentEvent],
    cancellation_token: CancellationToken,
    model_context: ChatCompletionContext,
) -> TextMessage:
    # Create a summary from the inner messages using an extra LLM call.
    input_messages = (
        [SystemMessage(content="你是一个会写代码和debug代码的智能体")]
        + thread_to_context(
            list(thread), agent_name, is_multimodal=model_client.model_info["vision"]
        )
        + [
            UserMessage(
                content="""
                上述文本是你最初收到的请求和你的历史消息。
                你需要为当前发生的所有的事情都生成一个总结概要，然后基于这些总结回答你收到的请求。
                如果过程中有代码被执行，请复制最终没有错误的代码。
                不要再赘述"你正在总结"，直接给出总结内容。""",
                source="user",
            )
        ]
    )

    # Re-initialize model context to meet token limit quota
    try:
        await model_context.clear()
        for msg in input_messages:
            await model_context.add_message(msg)
        token_limited_input_messages = await model_context.get_messages()
    except Exception:
        token_limited_input_messages = input_messages

    summary_result = await model_client.create(
        messages=token_limited_input_messages, cancellation_token=cancellation_token
    )
    assert isinstance(summary_result.content, str)
    code_block_list = _extract_markdown_code_blocks(summary_result.content)
    assert isinstance(summary_result.content, str)
    return TextMessage(
        source=agent_name,
        metadata={"internal": "yes", "has_codeblocks": "yes" if len(code_block_list) == 0 else "no"},
        content=summary_result.content,
    )
'''

class CodingDelegatorAgentConfig(BaseModel):
    name: str
    run_id: int
    model_client: ComponentModel
    description: str = ""
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
    这是一个代码智能体。它可以解释代码、下载代码、写代码、优化代码、重构代码、修复代码问题(debug)或回答代码相关的问题等任何和代码有关的任务。
    你可以同时指定代码的路经和代码文件，让此智能体将代码生成在指定路径中，或者在基于给定的代码文件内容进行修改代码、解释代码等操作。
    请将任何代码相关的任务交给此智能体。
    需要注意: 这个智能体只负责下载它生成的代码文件，不负责其他的下载任务。
    """

    system_prompt_coding_agent_template = """
    你是{name}, 一个代码智能体，但是你不会直接写代码，也不要写代码，你只是一个中间人，负责处理用户的输入，你的输出将被传递给另一个真正会写代码的智能体(不需要你来执行传递消息的动作，你只要按要求处理好用户的输入并按要求输出即可)。
    你要客观地分析用户的输入并提取相关的信息，然后将提取到的相关信息以JSON的格式输出。
    你位于服务器端，用户是客户端。
    用户也许会请求下载、保存文件(夹)，你没法直接把文件发送到用户所在客户端，但是你可以调用工具通知用户去下载服务器端上的这些文件或者文件夹。

    今天的日期是:{date_today}
    
    ## 专业能力
    ### 输入分析
    对于用户的输入，首先考虑如下问题:
    1. 用户的输入是否提出代码相关的请求，且包含代码的保存路径？ 如果是，你需要将代码的请求和用户要求的生成路径提取并且分开，但是不要篡改用户的请求。
    2. 用户的输入是否提出代码相关的请求，但是不含代码的保存路径？ 如果是，这时你只需要一字不差地的转述用户的输入。
    3. 用户的输入是否和代码请求无关，只是普通的交流或者回答问题？ 如果是，这时你只需要一字不差地的转述用户的输入。
    4. 用户的请求是否是需要调用工具？ 比如使用下载工具下载代码。如果是，这时你需要将用户的请求转换为工具调用。
    
    * 第2和第3种情况的处理方法是一样，你只需要一字不差地的转述用户的输入。
    
    用户输入的例子：
    - "帮我写一个Hello World的程序，并且保存在generate/test.py文件中" （代码请求，包含保存路径）
    - "用python写一个贪吃蛇游戏" （代码请求，但不包含保存路径）
    - "是的" (普通交流)
    - "用python" (回答代码问题，但不是提出代码请求)
    - "保存在/home/user/test.py文件中" (回答路径存储问题，但不是提出代码请求)
    
    ### 输出格式
    对应不同类型的用户输入请求输出分为以下几种情况:
    1. 对于[输入]中的第1，第2和第3种情况，你的输出要严格遵循以下JSON格式，且一定不要输出JSON格式以外的任何信息。
    
    ```json
    {{
        "request": "用户的请求",
        "save_path": "用户指定的生成路径，如果用户没有指定，则取空字符串",
    }}
    ```
    
    2. 对于[输入]的第4种情况，你的输出没有特别要求，只要正常的调用对应的工具就行。    
    
    ## 例子
    例子不会包含全部的情况，仅仅提供参考，你需要举一反三，根据上下文做出合适的判断。
    
    例子 1： 用户提出代码请求，你分析提取**代码请求**和**保存路径**，将**代码请求**和**保存路径**信息分开填入对应的JSON字段。
    输入： 帮我写一个Hello World的程序，并且保存在generate/test.py文件中。 
    输出：
        ```json
        {{
            "request": "帮我写一个Hello World的程序",
            "save_path": "generate/test.py"
        }}
        ```
    
    例子 2：用户提出了代码请求但是没有提到保存路径，`request`字段填入用户的请求，`save_path`字段取空字符串。
    输入： 用python写一个贪吃蛇游戏。 
    输出：
        ```json
        {{
            "request": "用python写一个贪吃蛇游戏",
            "save_path": ""
        }}
        ```
        
    例子 3：用户虽然提到了保存路径，但是这不是代码请求，可能是用户和另一个智能体的交流，你只需要一字不差地将用户的输入填入`request`字段，`save_path`字段取空字符串。
    输入： 保存在/home/user/test.py文件中。 
    输出：
        ```json
        {{
            "request": "保存在/home/user/test.py文件中",
            "save_path": ""
        }}
        ```
      
    ##  严格遵守的规则:
    - 严格尊重用户的输入请求，不要篡改用户的请求，或者加入你的主观意见。
    - 对于不用的输入类型，如果要求你输出JSON，则严格遵循[输出格式]规定的JSON格式，不要输出JSON格式以外的任何信息。
    - **保存路径**只能填入`save_path`字段，且只能包含路径，不要有任何其他文字说明或者信息。如果用户的输入没有包含路径要求，`save_path`字段必须取空字符串:\"\"。
    - **代码请求**只能填入`request`，且不要包含提取的**保存路径**信息。
    
    ### 重点注意:
    - 你不会写代码，也不要写代码，你只负责处理用户的输入，你的输出将被传递给另一个真正会写代码的智能体。
    - 你可以调用工具，调用工具不需要你输出JSON格式，只要正常调用工具就行。

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
            work_relative_dir (Path): Directory relative to {work_root} to save generated code files in the local filesystem. 
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
                auto_remove=True,
                stop_container=True,
                tty=True,
            )
        
        if self._code_manager:
            try:
                await self._code_manager.start()
            except Exception as e:
                logger.error(f"Error starting code manager: {e}")
                self._did_lazy_init = False

                
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
        if not self._did_lazy_init:
            yield Response(
                chat_message=TextMessage(
                    content="代码生成器依赖项初始化失败，无法生成代码",
                    source=self.name,
                    metadata={"internal": "yes"},
                )
            )
            return

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
            await self._model_context.clear()
            for msg in delegator_context:
                await self._model_context.add_message(msg)
            token_limited_context = await self._model_context.get_messages()
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
                    await self._model_context.add_message(
                        UserMessage(content=exception_message, source=agent_name)
                    )
                token_limited_context = await self._model_context.get_messages()
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
                    
                    token_limited_context = await self._model_context.get_messages()
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
                    
                    token_limited_context = await self._model_context.get_messages()
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
