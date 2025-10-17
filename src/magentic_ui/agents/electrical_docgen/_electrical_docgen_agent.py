from autogen_agentchat.agents import BaseChatAgent
import os
from magentic_ui.utils import thread_to_context

from typing import (
    Any,
    AsyncGenerator,
    List,
    Optional,
    Sequence,
    Union,
    Callable,
    Dict,
)

from autogen_agentchat.base import Response
from pydantic import BaseModel
from autogen_core import CancellationToken, Component, ComponentModel
from autogen_agentchat.messages import (
    ModelClientStreamingChunkEvent,
)
from autogen_core.model_context import (
    ChatCompletionContext,
    UnboundedChatCompletionContext,
)
from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    AssistantMessage,
    SystemMessage,
    UserMessage,
)
from autogen_agentchat.utils import remove_images
from autogen_agentchat.agents import BaseChatAgent

from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    TextMessage,
)

from ._prompts import (
    VALIDATION_AND_EXTRACTION_MESSAGE_PROMPT,
    URBAN_RAIL_TECHNICAL_SPECIFICATION_MANDATORY_ITEMS,
    URBAN_RAIL_TECHNICAL_SPECIFICATION_MISSING_ITEMS_TEMPLATE,
    project_design_paragraph_prompt_dict,
    CONCLUSION_AND_REPLY_PROMPT,
    urban_rail_traction_system_specification_dict,
)

from loguru import logger
from docxtpl import DocxTemplate
from pathlib import Path
import json


class GenDocxUseTemplate(object):
    """use template to generate doxc"""

    def __init__(self, template_file_path: str, output_file_path: str):

        self.template_file_path = template_file_path
        self.template_docx = DocxTemplate(Path(self.template_file_path))  # TODO
        self.output_file_path = Path(output_file_path)

    def gen_docx(self, variable_dict: Dict[str, Any], otput_file_name: str):
        """Generate Word document from template

        Args:
            variable_dict: Dictionary of template variables
            output_file_name: Output filename
        """

        self.template_docx.render(variable_dict)
        self.template_docx.save(self.output_file_path / otput_file_name)


class ElectrialcalDocGenConfig(BaseModel):
    """
    The declarative configuration for the ElectrialcalDocGen agent.

    Attributes:
        name: Agent name
        model_client: Model client component
        tools: List of tools, optional
        model_context: Model context component, optional
        description: Agent description
        system_message: System message, optional
        model_client_stream: Whether to use streaming model client
        structured_message_factory: Structured message factory component, optional
    """

    # pydantic 提供了具体的数据验证和序列化功能

    name: str
    model_client: ComponentModel
    tools: List[ComponentModel] | None = None
    model_context: ComponentModel | None = None
    description: str
    system_message: str | None = None
    model_client_stream: bool = False


class ElectrialcalDocGenAgent(BaseChatAgent, Component[ElectrialcalDocGenConfig]):
    """Electrical Documentation Generation Agent

    Core capabilities:
    ------------------
    - Generate documentation for electrical systems
    - Accept plain-language electrical design requirements
    usage example:
        electrialcal_docgen_agent = ElectrialcalDocGenAgent()
    """

    component_config_schema = ElectrialcalDocGenConfig
    component_provider_override = "magentic_ui.users._electriacal_docgen_agent"

    def __init__(
        self,
        name: str,
        model_client: ChatCompletionClient,
        work_dir: Path | str = "/workspace",
        bind_dir: Path | str | None = None,
        max_retries: int = 3,
        *,
        description: str = f"""
        ## 核心定位
        本agent是专业技术文档生成专家，由中车株洲所lamda实验室开发，严格遵循中车株洲所标准模板，自动化生成符合规范的设计方案说明书与技术规格说明书（.docx格式）。
        本助手会首先判断用户提供信息是否完整，如果缺少关键信息，将主动提示并引导补充必要内容。
        在生成过程中，如遇关键信息缺失，将主动提示并引导补充必要内容,即文档关键信息提示仅由调用本助手后提供，禁止杜撰关键信息；
        若信息完整，则直接输出高质量文档，并明确反馈“【xxx文档】已生成完成”。
        ## 必要信息收集规范
        {URBAN_RAIL_TECHNICAL_SPECIFICATION_MANDATORY_ITEMS}
        ## 信息缺失处理协议
        当检测到信息不完整时，必须严格使用以下模板向用户进行沟通，确保信息完整、逻辑清晰：
        {URBAN_RAIL_TECHNICAL_SPECIFICATION_MISSING_ITEMS_TEMPLATE}
        """,
        system_message: (
            str | None
        ) = """
        你是一个专业的 docx 文档生成助手，专注于高效、准确地生成各类项目文档，
        例如设计方案说明书, 技术规格说明书, 技术设计说明书等。在生成过程中，对于计划类文档中可能涉及的不明确或缺失的关键信息（如作者名称、文档类型、项目名称等），
        我会主动与您进行交互确认，以确保生成内容符合实际需要。
        整个过程无需依赖其他 agent 或联网搜索，由我独立完成。文档生成完成后，我将直接返回最终的 .docx 文件，代表任务结束。
        """,
        model_client_stream: bool = False,
        model_context: ChatCompletionContext | None = None,
    ):
        """
        Initialize Electrical Documentation Generation Agent

        Args:
            name: Agent name
            model_client: Chat completion client
            work_dir: Working directory path
            bind_dir: Bind directory path, optional
            max_retries: Maximum retry attempts
            description: Agent description
            system_message: System message
            model_client_stream: Whether to use streaming model client
            model_context: Model context, optional
        """
        super().__init__(name=name, description=description)
        self.work_dir = work_dir
        self.bind_dir = bind_dir
        self.max_retries = max_retries
        self.model_client = model_client
        self.model_client_stream = model_client_stream
        # Initialize system messages
        self._system_messages: List[SystemMessage] = []
        if system_message is None:
            self._system_messages = []
        else:
            self._system_messages = [SystemMessage(content=system_message)]
        # Initialize message history
        self.message_history: List[BaseChatMessage | BaseAgentEvent] = []
        # Initialize model context
        if model_context is not None:
            self._model_context = model_context
        else:
            self._model_context = UnboundedChatCompletionContext()
        # Initialize structured message factory

        # Document generator initialization
        current_file_path = __file__
        self.current_dir_os_path = os.path.dirname(os.path.abspath(current_file_path))
        self._variable_dict: Dict[str, Any] = {}

        # Agent state management
        self._state = "planning"  # Possible states: "planning", "generated", "revising", "completed"
        self._generated_doc_path = None  # Store generated document path
        self.data_response_planning = {}  # Planning phase data response

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        """Define message types produced by this agent"""
        return (TextMessage,)

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        async for message in self.on_messages_stream(messages, cancellation_token):
            if isinstance(message, Response):
                return message
        raise AssertionError("The stream should have returned the final result.")

    async def on_messages_stream(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        """
        Process the incoming messages with the ElectrialcalDocGen agent and yield events/responses as they happen.
        Args:
            messages: Input message sequence
            cancellation_token: Cancellation token

        Yields:
            Agent events, chat messages, or response objects
        """

        logger.debug("Enter ElectrialcalDocGenAgent", self._state)

        # Add the messages to the model context.
        self.message_history.extend(messages)
        inner_messages: List[BaseAgentEvent | BaseChatMessage] = []

        if self._state == "planning":
            # first step: jugement is contain all requirement message
            # get context prompt
            context_messages = self._thread_to_context(
                system_prompt=VALIDATION_AND_EXTRACTION_MESSAGE_PROMPT
            )
            temp_generate_key_list = [
                "complete",
                "message",
                "document_type",
                "project_name",
            ]
            # get json response using llm
            self.data_response_planning = await self._get_json_response(
                context_messages,
                lambda data: self._validation_json(temp_generate_key_list, data),
                cancellation_token,
            )
            if self.data_response_planning["complete"] == False:

                # yeild response to manager
                yield Response(
                    chat_message=TextMessage(
                        content=self.data_response_planning["message"],
                        source=self.name,
                    ),
                    inner_messages=inner_messages,
                )
                return
            elif (
                self.data_response_planning["complete"] == True
            ):  # generate data_response successful
                if self.data_response_planning["document_type"] not in [
                    "方案设计说明书",
                    "技术规格说明书",
                ]:
                    raise ValueError("JSON document_type 字段值无效")
                self._state = "generated"
            else:
                logger.debug("Invalid _data_response value.")
                raise ValueError("Invalid _data_response value.")

        if self._state == "generated":

            output_filename = ""
            if self.data_response_planning["document_type"] == "方案设计说明书":
                # step 1: genernate all paragraphs, results save as self._variable_dict中
                await self.generate_all_paragraphs(
                    project_design_paragraph_prompt_dict,
                    self._variable_dict,
                    cancellation_token,
                )
                # step 2: fill vars to docx use template, save file in output_filename
                self.generator = GenDocxUseTemplate(
                    os.path.join(
                        self.current_dir_os_path,
                        "docx_template/0_系统部件方案设计说明书.docx",
                    ),
                    str(self.work_dir),
                )
                # TODO  optimize filename
                output_filename = f"{self.data_response_planning.get('project_name', "未命名")}{self.data_response_planning.get('document_type', None)}.docx"
                self.generator.gen_docx(self._variable_dict, output_filename)

            elif self.data_response_planning["document_type"] == "技术规格说明书":

                await self.generate_all_paragraphs(
                    urban_rail_traction_system_specification_dict,
                    self._variable_dict,
                    cancellation_token,
                )
                # save docx
                self.generator = GenDocxUseTemplate(
                    os.path.join(
                        self.current_dir_os_path,
                        "docx_template/1_城轨_系统技术规格说明书.docx",
                    ),
                    str(self.work_dir),
                )
                output_filename = f"{self.data_response_planning.get('project_name', "未命名")}{self.data_response_planning.get('document_type', None)}.docx"
                self.generator.gen_docx(self._variable_dict, output_filename)
            else:
                # invalid document_type
                logger.debug(
                    "Invalid document_type",
                    self.data_response_planning["document_type"],
                )
                yield Response(
                    chat_message=TextMessage(
                        content="生成文档失败，请重新确认用户输入信息，重新规划生成文档。",
                        source=self.name,
                    ),
                    inner_messages=inner_messages,
                )
                return
            # step 3: yeild response to manager
            from docx import Document

            docx_obj = Document(os.path.join(str(self.work_dir), output_filename))
            docx_text = "\n".join([paragraph.text for paragraph in docx_obj.paragraphs])

            # yeild generate docx response to managetr
            # prepare llm message
            conclusion_prompt = CONCLUSION_AND_REPLY_PROMPT.format(
                docx_content=docx_text
            )
            conclusion_prompt_messages = [SystemMessage(content=conclusion_prompt)]
            response_content = await self.model_client.create(
                conclusion_prompt_messages,
                json_output=(
                    True if self.model_client.model_info["json_output"] else False
                ),
                cancellation_token=cancellation_token,
            )
            assert isinstance(response_content.content, str)
            response_content_str = self._clean_response_content(
                response_content.content
            )
            # yield response_content
            yield Response(
                chat_message=TextMessage(
                    content=response_content_str,
                    source=self.name,
                ),
                inner_messages=[],
            )
            self._state = "revising"

        # NEW: 添加修订逻辑
        if self._state == "revising":
            self._state = "planning"
            pass

    def _thread_to_context(
        self,
        system_prompt: str,
        messages: Optional[List[BaseChatMessage | BaseAgentEvent]] = None,
    ) -> List[LLMMessage]:
        """Convert the message thread to a context for the model."""
        #
        chat_messages: List[BaseChatMessage | BaseAgentEvent] = (
            messages if messages is not None else self.message_history
        )

        context_messages: List[LLMMessage] = []
        # add system_prompt
        context_messages.append(SystemMessage(content=system_prompt))
        # add context messages
        context_messages.extend(
            thread_to_context(
                messages=chat_messages, agent_name=self._name, is_multimodal=False
            )
        )

        return context_messages

    async def _get_json_response(
        self,
        messages: List[LLMMessage],
        validate_json: Callable[[Dict[str, Any]], bool],
        cancellation_token: CancellationToken,
    ) -> Dict[str, Any]:
        """Get a JSON response from the model client.
        Args:
            messages (List[LLMMessage]): The messages to send to the model client.
            validate_json (callable): A function to validate the JSON response. The function should return True if the JSON response is valid, otherwise False.
            cancellation_token (CancellationToken): A token to cancel the request if needed.
        """
        retries = 0
        exception_message = ""
        try:
            while retries < self.max_retries:
                # Re-initialize model context to meet token limit quota
                await self._model_context.clear()
                for msg in messages:
                    await self._model_context.add_message(msg)
                if exception_message != "":
                    await self._model_context.add_message(
                        UserMessage(content=exception_message, source=self._name)
                    )
                token_limited_messages = await self._model_context.get_messages()

                response = await self.model_client.create(
                    token_limited_messages,
                    json_output=(
                        True if self.model_client.model_info["json_output"] else False
                    ),
                    cancellation_token=cancellation_token,
                )

                assert isinstance(response.content, str)

                try:
                    response.content = self._clean_response_content(response.content)
                    json_response = json.loads(response.content)
                    # Use the validate_json function to check the response
                    if validate_json(json_response):
                        return json_response
                    else:
                        exception_message = "JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象。"
                        logger.debug(
                            f"JSON响应的验证失败: {json_response}, 正在重试 ({retries}/{self.max_retries})"
                        )
                except json.JSONDecodeError as e:
                    # json_response = extract_json_from_string(response.content)
                    # if json_response is not None:
                    #     if validate_json(json_response):
                    #         return json_response
                    #     else:
                    #         exception_message = "JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象。"
                    # else:
                    #     exception_message = f"JSON响应的解析失败，正在重试。你必须从响应中返回一个有效JSON对象。 错误: {e}"
                    logger.debug(
                        f"JSON响应的解析失败，正在重试 ({retries}/{self.max_retries})"
                    )
                retries += 1
            logger.debug(
                "多次尝试后，无法获得有效的JSON响应",
                internal=False,
            )
            raise ValueError("多次尝试后，无法获得有效的JSON响应")
        except Exception as e:
            logger.debug(f"Orchestrator遇到错误: {e}", internal=False)
            raise

    def _validation_json(
        self, project_designed_generate_list: list[str], data_response: dict[str, Any]
    ) -> bool:
        """validation json response function"""
        try:
            for k in project_designed_generate_list:
                if k not in data_response:
                    return False
                val = data_response.get(k)
                if k == "complete":
                    if isinstance(val, str):
                        val = val.lower()
                        if val == "true":
                            data_response[k] = True
                        elif val == "false":
                            data_response[k] = False
                        else:
                            data_response[k] = None
                else:
                    if val is not None and not isinstance(val, str):
                        data_response[k] = None
            return True
        except Exception:
            return False

    async def generate_all_paragraphs(
        self,
        paragraph_prompt_dict: Dict[str, Any],
        output_dict: Dict[str, Any],
        cancellation_token: CancellationToken,
    ) -> bool:
        """Generate all paragraph content

        Args:
            paragraph_prompt_dict: Paragraph prompt dictionary
            output_dict: Output dictionary for storing generated paragraph content
            cancellation_token: Cancellation token

        Returns:
            Whether generation was successful
        """
        # Iterate through all paragraph keys and generate content one by one
        for temp_generate_key in list(paragraph_prompt_dict.keys()):
            temp_generate_key_list = [temp_generate_key]
            # construct context for llm
            context_messages = self._thread_to_context(
                system_prompt=paragraph_prompt_dict[temp_generate_key]
            )
            # get json response result using llm
            data_response_generated = await self._get_json_response(
                context_messages,
                lambda data: self._validation_json(temp_generate_key_list, data),
                cancellation_token,
            )
            #
            for key in temp_generate_key_list:
                output_dict[key] = data_response_generated[key]
        return True

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        await self._model_context.clear()
        self._state = "planning"
        self.message_history = []

    async def _generate_document(self, variable_dict: Dict[str, Any]):
        """Generate document (internal method)

        Args:
            variable_dict: Template variable dictionary
        """
        output_filename = (
            f"{variable_dict.get('_coverpage_Project_Name', 'document')}.docx"
        )
        self.generator.gen_docx(variable_dict, output_filename)
        return

    def _clean_response_content(self, content: str) -> str:
        """Clean response content by removing markers and code blocks

        Args:
            content: Raw response content

        Returns:
            Cleaned content
        """
        content = content.strip()
        # Remove thinking markers if present
        if "</think>" in content:
            content = content.split("</think>")[-1]
            content = content.strip()
        # Remove markdown code block markers
        if "```json" in content:
            content = content.split("```json")[-1]
            content = content.strip()

        keywords = ["```", "markdown"]  # remove keywords of content in start and end
        for keyword in keywords:
            if content.startswith(keyword):
                content = content[len(keyword) :].strip()
            if content.endswith(keyword):
                content = content[: -len(keyword)].strip()
        return content.strip()

    @staticmethod
    def _get_compatible_context(
        model_client: ChatCompletionClient, messages: List[LLMMessage]
    ) -> Sequence[LLMMessage]:
        """Ensure that the messages are compatible with the underlying client, by removing images if needed."""
        if model_client.model_info["vision"]:
            return messages
        else:
            return remove_images(messages)

    @classmethod
    async def _call_llm(
        cls,
        model_client: ChatCompletionClient,
        model_client_stream: bool,
        system_messages: List[SystemMessage],
        model_context: ChatCompletionContext,
        agent_name: str,
        cancellation_token: CancellationToken,
        output_content_type: type[BaseModel] | None,
    ) -> AsyncGenerator[Union[CreateResult, ModelClientStreamingChunkEvent], None]:
        """
        Perform a model inference and yield either streaming chunk events or the final CreateResult.
        """

        all_messages = await model_context.get_messages()

        llm_messages = cls._get_compatible_context(
            model_client=model_client, messages=system_messages + all_messages
        )

        if model_client_stream:

            model_result: Optional[CreateResult] = None
            async for chunk in model_client.create_stream(
                llm_messages,
                tools=[],
                json_output=output_content_type,
                cancellation_token=cancellation_token,
            ):
                if isinstance(chunk, CreateResult):
                    model_result = chunk
                elif isinstance(chunk, str):
                    yield ModelClientStreamingChunkEvent(
                        content=chunk, source=agent_name
                    )
                else:
                    raise RuntimeError(f"Invalid chunk type: {type(chunk)}")
            if model_result is None:
                raise RuntimeError("No final model result in streaming mode.")
            yield model_result
        else:
            model_result = await model_client.create(
                llm_messages,
                tools=[],
                cancellation_token=cancellation_token,
                json_output=output_content_type,
            )
            yield model_result


async def main():

    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from autogen_core.models import ModelFamily
    from autogen_agentchat.agents import UserProxyAgent
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_agentchat.conditions import TextMentionTermination

    model_client = OpenAIChatCompletionClient(
        model="qwq-32b",
        base_url="http://36.103.239.236:8000/v1/",
        api_key="placeholder",
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": False,
            "family": ModelFamily.R1,
            "structured_output": True,
        },
    )
    current_file_path = __file__
    current_dir_os_path = os.path.dirname(os.path.abspath(current_file_path))
    electrial_gendoc = ElectrialcalDocGenAgent(
        "electrial_gendoc",
        model_client,
        work_dir=current_dir_os_path,
        model_client_stream=True,
    )

    def input_func(prompt: str = "") -> str:
        """终端用户输入"""
        return input(prompt)

    # Create the critic agent.
    critic_agent = UserProxyAgent(
        name="user",
        input_func=input_func,
        description="接受用户的信息。 Respond with 'APPROVE' to when your feedbacks are addressed.",
    )
    text_termination = TextMentionTermination("APPROVE")

    # Create a team with the primary and critic agents. primary_agent, critic_agent,
    team = RoundRobinGroupChat(
        [electrial_gendoc, critic_agent], termination_condition=text_termination
    )
    # Use `asyncio.run(...)` when running in a script.
    from autogen_agentchat.ui import Console

    await Console(team.run_stream(task="帮我生成一个技术规格书说明书"))


if __name__ == "__main__":
    logger.debug("run _electrical_docgen_agent.py")
    import asyncio

    asyncio.run(main())
