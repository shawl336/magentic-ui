import asyncio
from ctypes import Union
from pathlib import Path
import shutil
from typing import AsyncGenerator, List, Literal, Sequence, Optional, Dict
import re, json
from typing import Any, Mapping
import uuid
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

from autogen_agentchat.agents import BaseChatAgent, AssistantAgent
from autogen_core.code_executor import CodeBlock, CodeExecutor
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
from autogen_ext.code_executors.local import LocalCommandLineCodeExecutor

from ..utils import thread_to_context

from ..approval_guard import BaseApprovalGuard
from ..guarded_action import ApprovalDeniedError, TrivialGuardedAction
from ..tools.mcp import AggregateMcpWorkbench, NamedMcpServerParams
from ..docker_manager import DockerManager
from autogen_core.tools import ToolSchema
from ..teams.orchestrator._utils import extract_json_from_string
# import logging
# from autogen_agentchat import logger_NAME


'''
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
    model_client: ComponentModel
    description: str = """
    一个可以写代码和执行代码的智能体。它可以解释代码、写代码、优化代码、重构代码、修复代码问题(bug)或回答代码相关的问题等任何和代码有关的任务。
    你可以同时指定代码的路经和代码文件，让此智能体将代码生成在指定路径中，或者在基于给定的代码文件内容进行修改代码、解释代码等操作。
    请将任何代码相关的任务交给此智能体。
    """
    max_reties: int = 3
    summarize_output: bool = False
    coding_tools: List[NamedMcpServerParams]
    coding_provider: str
    work_dir: Path
    bind_dir: Path
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
    coding_workbench: AggregateMcpWorkbench
    
    DEFAULT_DESCRIPTION = """
    一个可以写代码和执行代码的智能体。它可以解释代码、写代码、优化代码、重构代码、修复代码问题(bug)或回答代码相关的问题等任何和代码有关的任务。
    你可以同时指定代码的路经和代码文件，让此智能体将代码生成在指定路径中，或者在基于给定的代码文件内容进行修改代码、解释代码等操作。
    请将任何代码相关的任务交给此智能体。
    """

    system_prompt_coding_agent_template = """
    你是一个中间人，负责客观地分析用户的输入并提取相关的信息，然后通过JSON格式化地输出提取到的相关信息。
    
    <输入>
    用户的输入的在正常情况下都是代码相关的任务，并且可能包含代码的生成路径，你需要将代码的需求和用户要求的生成路径提取并且分开。
    比如："帮我写一个Hello World的程序，并且保存在generate/test.py文件中"
    </输入>

    <输出>
    你的输出需要遵循如下JSON格式，且一定不要输出JSON格式以外的任何信息。:
    
    ```json
    {{
        "request": "用户的需求",
        "save_path": "用户指定的生成路径，如果用户没有指定，则取空字符串:\"\"",
    }}
    ```
    </输出>
    
    
    <例子>
    输入： 帮我写一个Hello World的程序，并且保存在generate/test.py文件中。 
    输出：
        ```json
        {{
            "response": "帮我写一个Hello World的程序",
            "save_path": "generate/test.py"
        }}
        ```
    </例子>

      
    <严格遵守的规则>:
    - 严格尊重用户的输入需求，不要篡改用户的需求，或者加入你的主观意见。
    - 严格遵循**输出**规定的JSON格式，不要输出JSON格式以外的任何信息。
    - {{save_path}}字段只能包含路径，不要有任何其他文字说明或者信息。如果用户的输入没有包含路径要求，{{save_path}}字段必须取空字符串:\"\"。
    </严格遵守的规则>

    """
    
    system_prompt_coding_agent_template_tool_based = """
    你是一个中间人，负责客观地分析用户的输入并提取相关的信息，并将用户的需求转换为工具调用。
    
    注意要求:
    - 你不要加入任何主观意见或者解释，直接将用户的需求转换工具调用。
    - 你不要输出任何其他信息，只输出工具调用。
    """

    def __init__(
        self,
        name: str,
        model_client: ChatCompletionClient,
        coding_tools: List[NamedMcpServerParams],
        work_dir: Path,
        bind_dir: Path,
        coding_provider: str,
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
            description (str, optional): Description of the agent's capabilities. Default: DEFAULT_DESCRIPTION.
            max_reties (int, optional): Maximum number of tring generate json response. Default: 2.
            summarize_output (bool, optional): Whether to summarize code execution results. Default: False.
            code_executor (Optional[CodeExecutor], optional): It does not execute code curerenly, 
                but utilize the code_executor to run the coding provider MCP server. Default: None.
            work_dir (Path | str | None, optional): Working directory for code execution. Default: None.
            bind_dir (Path | str | None, optional): Working directory in Docker container. Default: None.
        """
        super().__init__(name, description)
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
        self._work_dir = work_dir
        self._bind_dir = bind_dir
        self._coding_provider = coding_provider
                
        self.coding_workbench = AggregateMcpWorkbench(named_server_params=coding_tools)
            
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
        logger.info("Closing CodingDelegatorAgent...")
        # Remove the work directory if it was created.
        if self._cleanup_work_dir and self._work_dir.exists():
            await asyncio.to_thread(shutil.rmtree, self._work_dir)
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
        last_message_received: BaseChatMessage = messages[-1]
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
                bind_dir=self._bind_dir,
                model_client=self._model_client,
                workbench=self.coding_workbench,
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
                    content=f"coding智能体发生如下错误： {e}",
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
        bind_dir: Path,
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

        # create an LLM context from system message, global chat history, and inner messages
        context = [SystemMessage(content=system_prompt)] + thread_to_context(
            current_thread,
            agent_name,
            is_multimodal=model_client.model_info["vision"],
        )

        # Re-initialize model context to meet token limit quota
        try:
            await model_context.clear()
            for msg in context:
                await model_context.add_message(msg)
            token_limited_context = await model_context.get_messages()
        except Exception:
            token_limited_context = context
        
        # check the mcp tools match the coding_provider
        try:
            tools: List[ToolSchema] = await workbench.list_tools()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error when listing MCP tools: {e}")
            raise Exception("Error connecting to coding provider") from e
        except Exception as e:
            logger.error(f"Unexpected error when listing MCP tools: {e}")
            raise Exception("Error connecting to coding provider") from e
            
        coding_tool: ToolSchema | None = None
        # find the expeceted coding_tool
        
        for tool in tools:
            if self.validate_tool_parameters(tool):
                coding_tool = tool
                break 
        else:
            raise ValueError(f"Coding tool {coding_provider} not found")
        
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
                    token_limited_context[-1:],
                    json_output=True
                    if model_client.model_info["json_output"]
                    else False,
                    cancellation_token=cancellation_token,
                )
                assert isinstance(delegated_result.content, str)
                try:
                    yield TextMessage(
                            source=agent_name + "-llm",
                            metadata={"type": "potential_code"},
                            content=delegated_result.content,
                        )
                    delegated_json_response = json.loads(delegated_result.content)
                    # Use the validate_json function to check the response
                    if self.validate_output_json(delegated_json_response):
                        break
                    else:
                        exception_message = "Validation failed for JSON response, retrying. You must return a valid JSON object parsed from the response."
                        logger.debug(
                            f"Validation failed for JSON response: {delegated_json_response}, retrying ({retries}/{max_json_retries})"
                        )
                except json.JSONDecodeError as e:
                    delegated_json_response = extract_json_from_string(delegated_result.content)
                    if delegated_json_response is not None:
                        if self.validate_output_json(delegated_json_response):
                            break
                        else:
                            exception_message = "Validation failed for JSON response, retrying. You must return a valid JSON object parsed from the response."
                    else:
                        logger.error(f"Failed to parse JSON response, retrying. {e}, {delegated_result.content}")
                        exception_message = f"Failed to parse JSON response, retrying. You must return a valid JSON object parsed from the response. Error: {e}"
                    logger.debug(
                        f"Failed to parse JSON response, retrying ({retries}/{max_json_retries})"
                    )
                retries += 1
            else:
                raise ValueError(f"Failed to get a valid JSON response after {max_json_retries} retries")
        except Exception as e:
            logger.error(f"Error in CodingDelegatorAgent: {e}")
            raise
        
        assert delegated_json_response is not None
        # call the coding tool
        
        # currently, not allowing customized generating path
        delegated_json_response["save_path"] = str(bind_dir)
        # delegated_json_response will not be appended to the chat_history
        delegated_json_response["request"] = "\n".join(i.content for i in context if isinstance(i.content, str)) + "\n" + delegated_json_response["request"]
        
        try:
            tool_call_result = await workbench.call_tool(
                coding_tool.get("name"),
                delegated_json_response,
                cancellation_token=cancellation_token,
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error when calling MCP tool: {e}")
            raise Exception("Error calling the coding provider") from e
            
        except Exception as e:
            logger.error(f"Unexpected error when calling MCP tool: {e}")
            raise Exception("Unexpected Error calling the coding provider") from e
        
        yield TextMessage(
            content = tool_call_result.to_text(),
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

    def _to_config(self) -> CodingDelegatorAgentConfig:
        """Convert the agent's state to a configuration object."""
        return CodingDelegatorAgentConfig(
            name=self.name,
            model_client=self._model_client.dump_component(),
            coding_tools=self.coding_workbench.server_params,
            work_dir=self._work_dir,
            bind_dir=self._bind_dir,
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
            model_client=ChatCompletionClient.load_component(config.model_client),
            coding_tools=config.coding_tools,  # Convert single tool to list
            work_dir=config.work_dir,
            bind_dir=config.bind_dir,
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
