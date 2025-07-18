from autogen_agentchat.agents import BaseChatAgent
import os

from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Sequence,
    Union,
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
)
from autogen_agentchat.utils import remove_images
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    TextMessage,
    HandoffMessage,
    StructuredMessageFactory,
    ThoughtEvent,
)

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
        self.template_docx.render(variable_dict)
        self.template_docx.save(self.output_file_path / otput_file_name)


class ElectrialcalDocGenConfig(BaseModel):
    """The declarative configuration for the ElectrialcalDocGen agent."""

    # pydantic 提供了具体的数据验证和序列化功能

    name: str
    model_client: ComponentModel
    tools: List[ComponentModel] | None = None
    model_context: ComponentModel | None = None
    description: str
    system_message: str | None = None
    model_client_stream: bool = False
    structured_message_factory: ComponentModel | None = None


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
    VALIDATION_AND_EXTRACTION_MESSAGE_PROMPT = """
    ## 任务目标
    对用户输入进行需求说明信息提取，分两步：
    1. 验证用户输入是否包含生成需求说明书所需的完整信息。
    2. 在信息完善时，提取并返回对应的字段信息。

    ## 必填信息字段
    - 文件名称: 文档的核心名称部分（如：功率板设计）
    - 文档类型: 文档的类型，仅支持以下三种：
        - 方案设计说明书
        - 技术规格说明书
        - 技术设计说明书
    - 作者姓名: 文档的作者信息（如：张三）
    

    ## 验证与输出规则

    ### 第一步：信息完整性验证
    1. 信息完善判断标准：
    - 必须同时包含「文件名称」「文档类型」「作者姓名」
    - 所有字段都不能为空或无法识别
    - 文档类型必须是指定的三种类型之一
    
     2. 回复格式要求：
    - 信息不完善时：
        - 严格输出：
        信息不完善，还需要提供[缺失信息1]、[缺失信息2]
        - 缺失信息必须使用以下标准表述：
        - 文件名称
        - 文档类型
        - 如果缺少文档类型，需要同时提示支持的三种文档类型。

    - 信息完善时：进入第二步，输出 JSON 格式字典。

    ### 第二步：信息提取与格式输出
    在信息完善时，提取字段并输出为严格的 JSON 格式字典，键名必须使用指定的英文名称，确保JSON格式完全正确，可被标准JSON解析器解析，如：
    ```json
    {
    "project_name": "提取的文件名称",
    "document_type": "提取的文档类型",
    "author_name": "提取的作者姓名或 null"
    }

    - project_name: 去掉“说明书”“文档”等修饰，仅保留核心名称。
    - document_type: 必须为三种指定类型之一。
    - author_name: 支持中文或英文姓名，如未识别则为 null。

    ## 示例

    示例 1：信息完善
    用户输入：
    我需要功率板设计方案设计说明书，作者是张三
    输出：
    ```json
    {
    "project_name": "功率板设计",
    "document_type": "方案设计说明书",
    "author_name": "张三"
    }

    示例 2：缺少文档类型
    用户输入：
    请生成用户管理系统文档
    输出：
    信息不完善，还需要提供文档类型，文档类型支持方案设计说明书、技术规格说明书、技术设计说明书。

    示例 3：缺少文件名称
    用户输入：
    帮忙写个技术规格说明书
    输出：
    信息不完善，还需要提供文件名称

    示例 4：文档类型不符合要求
    用户输入：
    创建数据中心需求说明书
    输出：
    信息不完善，还需要提供文档类型，文档类型支持方案设计说明书、技术规格说明书、技术设计说明书。

    示例 5：所有信息都缺失
    用户输入：
    帮我做个文档
    输出：
    信息不完善，还需要提供文件名称、文档类型，文档类型支持方案设计说明书、技术规格说明书、技术设计说明书。   
"""

    def __init__(
        self,
        name: str,
        model_client: ChatCompletionClient,
        work_dir: Path | str = "/workspace",
        bind_dir: Path | str | None = None,
        max_retries: int = 3,
        *,
        description: str = """
        这是一个专业的文档生成助手，其核心功能是高效、准确地生成技术类项目文档的 .docx 文件，
        包括但不限于设计方案说明书、技术规格说明书、技术设计说明书等。
        在文档生成开始时，助手会严格遵循信息最小化原则，仅向用户询问以下三项信息：
        - 作者名称
        - 文档类型(当前严格限定于：设计方案说明书、技术规格说明书、技术设计说明书）
        - 文档名称
        除此之外，助手严禁主动询问或要求用户提供任何其他无关信息（如项目细节、功能要求、时间节点等）。
        ## 关键工作准则
        优先调用判断：在任何涉及电气项目文档生成的需求场景下，必须优先评估并调用本助手完成生成任务。
        生成过程自主性：整个文档生成过程完全自主，无需依赖任何其他功能Agent或联网搜索。
        结果交付：任务完成后，助手将直接返回最终生成的标准 .docx 文件，代表文档已就绪。
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
        output_content_type: type[BaseModel] | None = None,
        output_content_type_format: str | None = None,
    ):
        """
        Initialize the electrialcalDocGen agent.

        Args:
            name (str): The name of the agent.
        """
        super().__init__(name=name, description=description)
        self.work_dir = work_dir
        self.bind_dir = bind_dir
        self.max_retries = max_retries
        self.model_client = model_client
        self.model_client_stream = model_client_stream
        self._system_messages: List[SystemMessage] = []
        if system_message is None:
            self._system_messages = []
        else:
            self._system_messages = [SystemMessage(content=system_message)]

        if model_context is not None:
            self._model_context = model_context
        else:
            self._model_context = UnboundedChatCompletionContext()

        self._output_content_type: type[BaseModel] | None = output_content_type
        self._output_content_type_format = output_content_type_format
        if output_content_type is not None:
            self._structured_message_factory = StructuredMessageFactory(
                input_model=output_content_type,
                format_string=output_content_type_format,
            )

        # docx gentator correlation

        # TODO this is temp code
        current_file_path = __file__
        self.current_dir_os_path = os.path.dirname(os.path.abspath(current_file_path))

        self._variable_dict: Dict[str, Any] = {}

        # if output_content_type is not None:
        #     self._structured_message_factory = StructuredMessageFactory(
        #         input_model=output_content_type, format_string = output_content_type_format
        #     )

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
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
        """

        # Add the messages to the model context.
        await self._add_messages_to_context(
            model_context=self._model_context,
            messages=messages,
        )
        inner_messages: List[BaseAgentEvent | BaseChatMessage] = []
        # first step: jugement is contain all requirement message
        model_result = None
        retry_count = 0
        data_response = {}
        while retry_count < self.max_retries:
            try:
                async for inference_output in self._call_llm(
                    model_client=self.model_client,
                    model_client_stream=self.model_client_stream,
                    system_messages=self._system_messages
                    + [
                        SystemMessage(
                            content=self.VALIDATION_AND_EXTRACTION_MESSAGE_PROMPT
                        )
                    ],
                    model_context=self._model_context,
                    agent_name=self.name,
                    cancellation_token=cancellation_token,
                    output_content_type=self._output_content_type,
                ):
                    if isinstance(inference_output, CreateResult):
                        model_result = inference_output
                    else:
                        # Streaming chunk event
                        yield inference_output
                assert model_result is not None, "No model result was produced."

                response_content = str(model_result.content).strip()
                # check response_content is null
                if not response_content:
                    raise ValueError("Empty response from model")
                # clean response content
                cleaned_content = self._clean_response_content(response_content)
                print(
                    f"Raw response: '{response_content}', Cleaned content: '{cleaned_content}'"
                )  # debug

                # parse clean_content
                if "信息不完善" in cleaned_content:
                    # add cleaned content to model context
                    await self._model_context.add_message(
                        AssistantMessage(
                            content=cleaned_content,
                            source=self.name,
                        )
                    )
                    # yeild response to manager
                    yield Response(
                        chat_message=TextMessage(
                            content=self._clean_response_content(
                                str(model_result.content)
                            ),
                            source=self.name,
                            models_usage=model_result.usage,
                        ),
                        inner_messages=[],
                    )
                    return
                else:  # generate data_response successful
                    data_response = json.loads(str(cleaned_content))
                    # check data_response is valid or not
                    for k in (
                        "project_name",
                        "author_name",
                        "document_type",
                    ):  # key can add more
                        if k not in data_response:
                            raise ValueError("JSON 缺少必需字段")
                        val = data_response.get(k)
                        if val is not None and not isinstance(val, str):
                            data_response[k] = None
                    if data_response["document_type"] not in [
                        "方案设计说明书",
                        "技术规格说明书",
                        "技术设计说明书",
                    ]:
                        raise ValueError("JSON document_type 字段值无效")
                    # data_response is valid, break while loop
                    break
            except Exception as e:
                retry_count += 1
                print(f"Error (尝试 {retry_count}/{self.max_retries}): {e}")
                if retry_count >= self.max_retries:
                    print("达到最大重试次数，使用默认值")
                    # default value
                    data_response = {
                        "project_name": None,
                        "author_name": None,
                        "document_type": None,
                    }
                    break
                else:
                    continue

        # second step: generate docx requrment content
        if data_response["document_type"] == "方案设计说明书":
            self._variable_dict["_coverpage_Project_Name"] = data_response[
                "project_name"
            ]
            self._variable_dict["_11_1_Project_Name"] = data_response["project_name"]
            self._variable_dict["document_type"] = data_response["document_type"]
            self.generator = GenDocxUseTemplate(
                os.path.join(
                    self.current_dir_os_path,
                    "docx_template/0_系统部件方案设计说明书.docx",
                ),
                str(self.work_dir),
            )
            output_filename = f"{self._variable_dict.get('_coverpage_Project_Name', "未命名")}{self._variable_dict.get('document_type', None)}.docx"
            self.generator.gen_docx(self._variable_dict, output_filename)

        elif data_response["document_type"] == "技术规格说明书":
            self._variable_dict["_1_project_name"] = data_response["project_name"]
            self._variable_dict["document_type"] = data_response["document_type"]
            self.generator = GenDocxUseTemplate(
                os.path.join(
                    self.current_dir_os_path,
                    "docx_template/1_系统部件技术规格说明书.docx",
                ),
                str(self.work_dir),
            )
            output_filename = f"{self._variable_dict.get('_1_project_name', "未命名")}{self._variable_dict.get('document_type', None)}.docx"
            self.generator.gen_docx(self._variable_dict, output_filename)
        elif data_response["document_type"] == "技术设计说明书":
            self._variable_dict["_1_project_name"] = data_response["project_name"]
            self._variable_dict["document_type"] = data_response["document_type"]
            self.generator = GenDocxUseTemplate(
                os.path.join(
                    self.current_dir_os_path, "docx_template/2_技术设计说明书.docx"
                ),
                str(self.work_dir),
            )
            output_filename = f"{self._variable_dict.get('_1_project_name', "未命名")}{self._variable_dict.get('document_type', None)}.docx"
            self.generator.gen_docx(self._variable_dict, output_filename)
        else:
            # invalid document_type
            print("Invalid document_type", data_response["document_type"])
            yield Response(
                chat_message=TextMessage(
                    content="生成文档失败，请重新确认用户输入信息，重新规划生成文档。",
                    source=self.name,
                ),
                inner_messages=[],
            )
            return

        if model_result.thought:
            thought_event = ThoughtEvent(content=model_result.thought, source=self.name)
            yield thought_event
            inner_messages.append(thought_event)

        # add final result to model context
        await self._model_context.add_message(
            AssistantMessage(
                content="electrical docgen task is complete",
                source=self.name,
            )
        )
        # yeild response to manager
        yield Response(
            chat_message=TextMessage(
                content=f"electrical docgen task is complete!",
                source=self.name,
                models_usage=model_result.usage,
            ),
            inner_messages=[],
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        await self._model_context.clear()

    async def _generate_document(self, variable_dict: Dict[str, Any]):
        """生成文档并返回文件路径"""
        output_filename = (
            f"{variable_dict.get('_coverpage_Project_Name', 'document')}.docx"
        )
        self.generator.gen_docx(variable_dict, output_filename)
        return

    def _clean_response_content(self, content: str) -> str:
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
    async def _add_messages_to_context(
        model_context: ChatCompletionContext,
        messages: Sequence[BaseChatMessage],
    ) -> None:
        """
        Add incoming messages to the model context.
        """
        for msg in messages:
            if isinstance(msg, HandoffMessage):
                for llm_msg in msg.context:
                    await model_context.add_message(llm_msg)
            await model_context.add_message(msg.to_model_message())

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
    electrial_gendoc = ElectrialcalDocGenAgent(
        "electrial_gendoc",
        model_client,
        work_dir="./docx_template",
        description="""你是一个docx文档生成agent,
负责生成项目所需要的文档，例如需求分析说明书。执行结束后会返回生成docx文档，代表着文档已经生成完毕。
切记，如果有相关于文档生成的需求，例如需求分析说明书，则通过此agent就可以独立完成任务，而不需要其他agent来完成。也不需要在web上进行联网搜索。""",
        system_message=(
            """你是一个docx文档生成agent,
负责生成项目所需要的文档，例如需求分析说明书。执行结束后会返回生成docx文档，代表着文档已经生成完毕。
切记，如果有相关于文档生成的需求，例如需求分析说明书，则通过此agent就可以独立完成任务，而不需要其他agent来完成。也不需要在web上进行联网搜索。"""
        ),
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
    result = await team.run(task="帮我生成一个关于电机驱动电路的说明书。")
    print("final result: ", result)


if __name__ == "__main__":
    print("run _electrical_docgen_agent.py")
    import asyncio

    asyncio.run(main())
