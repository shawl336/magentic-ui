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
    AsyncGenerator,
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
from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    TextMessage,
    HandoffMessage,
    StructuredMessageFactory,
)
from ._prompts import (
   VALIDATION_AND_EXTRACTION_MESSAGE_PROMPT,
   technical_specification_paragraph_prompt_dict,
   project_design_paragraph_prompt_dict,
   CONCLUSION_AND_REPLY_PROMPT,

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

    def __init__(
        self,
        name: str,
        model_client: ChatCompletionClient,
        work_dir: Path | str = "/workspace",
        bind_dir: Path | str | None = None,
        max_retries: int = 3,
        *,
        description: str = """
        ## 核心定位
        本agent是专业技术文档生成专家，由中车株洲所lamda实验室开发，严格遵循中车株洲所标准模板，自动化生成符合规范的设计方案说明书与技术规格说明书（.docx格式）。
        在生成过程中，如遇关键信息缺失，将主动提示并引导补充必要内容,即文档关键信息仅由调用本助手后提供，禁止杜撰关键信息；
        若信息完整，则直接输出高质量文档，并明确反馈“【xxx文档】已生成完成”。
        ## 必要信息收集规范

        **仅限以下三项核心信息，严禁索要任何额外内容**：

        - 文档类型：方案设计说明书｜技术规格说明书
        - 文档名称：如"三相逆变器系统需求说明书"
        - 项目描述：如"三相逆变器系统采用'刺'型拓扑的10 kW三相逆变器，高效低谐波，全数字控制，无风扇户外运行，面向光伏储能车网互动。"

        ## 信息缺失处理协议

        当检测到信息不完整时，必须严格使用以下模板向用户进行沟通，确保信息完整、逻辑清晰：

        **请补充以下三项必要信息（请直接复制修改）：**

        - 文档类型：[方案设计说明书/技术规格说明书]
        - 文档名称：[请输入文档名称]
        - 项目描述：[请用一句话描述项目内容]

        **参考示例（可直接复制使用）**：

        - 文档类型：方案设计说明书
        - 文档名称：三相逆变器系统需求说明书
        - 项目描述：三相逆变器系统采用"刺"型拓扑的10 kW三相逆变器，高效低谐波，全数字控制，无风扇户外运行，面向光伏储能车网互动。
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
        ## 
        self._state = "planning"  # 可能状态: "planning", "generated", "revising", "completed"
        self._generated_doc_path = None  # 存储生成的文档路径
        self._data_response = None  # 存储提取的数据
        self.data_response_planning = {} # TODO, transition to docagentstate

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
        print("enter electrical doc gen", self._state)
        if self._state == "planning":
            # first step: jugement is contain all requirement message
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    # 确认用户是否提供文件类型
                    cleaned_content = await self.call_llm(
                        system_messages = [SystemMessage(content=VALIDATION_AND_EXTRACTION_MESSAGE_PROMPT)], 
                        model_context = self._model_context, 
                        cancellation_token = cancellation_token
                    )

                    self.data_response_planning = json.loads(str(cleaned_content))
                    self._validation_json(["complete", "message", "document_type"], self.data_response_planning)

                    # parse data_response    
                    if self.data_response_planning["complete"] == False:
                        # add cleaned content to model context
                        await self._model_context.add_message(
                            AssistantMessage(
                                content=self.data_response_planning["message"],
                                source=self.name,
                            )
                        )
                        # yeild response to manager
                        yield Response(chat_message=TextMessage(content = self.data_response_planning["message"], source=self.name, ), inner_messages=[], )
                        return
                    elif self.data_response_planning["complete"] == True:  # generate data_response successful
                        if self.data_response_planning["document_type"] not in ["方案设计说明书", "技术规格说明书",]:
                            raise ValueError("JSON document_type 字段值无效")
                        self._state = "generated"
                        break
                    else:
                        pass  
                except Exception as e:
                    retry_count += 1
                    print(f"Error (尝试 {retry_count}/{self.max_retries}): {e}")
                    if retry_count >= self.max_retries:
                        print("达到最大重试次数，使用默认值")
                        # default value
                        self.data_response_planning = {k: None for k in ["complete", "message", "document_type"]}
                        break
                    else:
                        continue
        
        if self._state == "generated":
            
            # 生成文档的变量
            output_filename = ''
            if self.data_response_planning["document_type"] == "方案设计说明书":
                
                # 生成所有段落
                await self.generate_all_paragraphs(project_design_paragraph_prompt_dict, self._variable_dict, cancellation_token)  
                
                # 保存文档
                self.generator = GenDocxUseTemplate(
                    os.path.join(
                        self.current_dir_os_path,
                        "docx_template/0_系统部件方案设计说明书.docx",
                    ),
                    str(self.work_dir),
                )
                output_filename = f"{self._variable_dict.get('_coverpage_Project_Name', "未命名")}{self.data_response_planning.get('document_type', None)}.docx"
                self.generator.gen_docx(self._variable_dict, output_filename)

            elif self.data_response_planning["document_type"] == "技术规格说明书":
                
                await self.generate_all_paragraphs(technical_specification_paragraph_prompt_dict, self._variable_dict, cancellation_token)  
                
                self.generator = GenDocxUseTemplate(
                    os.path.join(
                        self.current_dir_os_path,
                        "docx_template/1_系统部件技术规格说明书.docx",
                    ),
                    str(self.work_dir),
                )
                output_filename = f"{self._variable_dict.get('_1_project_name', "未命名")}{self.data_response_planning.get('document_type', None)}.docx"
                self.generator.gen_docx(self._variable_dict, output_filename)
            else:
                # invalid document_type
                print("Invalid document_type", self.data_response_planning["document_type"])
                yield Response(
                    chat_message=TextMessage(
                        content="生成文档失败，请重新确认用户输入信息，重新规划生成文档。",
                        source=self.name,
                    ),
                    inner_messages=[],
                )
                return
                # 生成生成内容

            # add final result to model context
            await self._model_context.add_message(
                AssistantMessage(
                    content="electrical docgen task is complete",
                    source=self.name,
                )
            )
            # yeild response to manager
            # TODO
            from docx import Document
            docx_obj = Document(os.path.join(str(self.work_dir), output_filename))
            docx_text = "\n".join([paragraph.text for paragraph in docx_obj.paragraphs])
            
            
            # 返回（总结+ 可以的操作+示例回复）
            cleaned_content = await self.call_llm(
                                system_messages = [SystemMessage(content = CONCLUSION_AND_REPLY_PROMPT.format(docx_content = docx_text))], 
                                model_context = UnboundedChatCompletionContext(), 
                                cancellation_token = cancellation_token
                            )
            yield Response(
                chat_message=TextMessage(
                    content=cleaned_content,
                    source=self.name,
                ),
                inner_messages=[],
            )
            self._state = "revising"
    
        # NEW: 添加修订逻辑
        if self._state == "revising":
            self._state = "planning"
            pass

    async def generate_all_paragraphs(self, paragraph_prompt_dict: Dict[str, Any], output_dict: Dict[str, Any], cancellation_token: CancellationToken) -> bool:
        
        data_response_generated: dict[str, Any] = {}
        for temp_generate_key in list(paragraph_prompt_dict.keys()):
            temp_generate_key_list = [temp_generate_key]
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    #生成正文内容
                    cleaned_content = await self.call_llm(
                        system_messages = [SystemMessage(content=paragraph_prompt_dict[temp_generate_key])], 
                        model_context = self._model_context, 
                        cancellation_token = cancellation_token
                    )
                    data_response_generated = json.loads(str(cleaned_content))

                    # check data_response is valid or not
                    self._validation_json(temp_generate_key_list, data_response_generated)
                    break
                except Exception as e:
                    retry_count += 1
                    print(f"Error (尝试 {retry_count}/{self.max_retries}): {e}")
                    if retry_count >= self.max_retries:
                        print("达到最大重试次数，使用默认值")
                        # default value
                        data_response_generated = {k: "" for k in list(paragraph_prompt_dict.keys())}
                        break
                    else:
                        continue
            for key in temp_generate_key_list:
                output_dict[key] = data_response_generated[key]
        return True
    def _validation_json(self, project_designed_generate_list: list[str], data_response: dict[str, Any]):
        for k in project_designed_generate_list:  # key can add more
            if k not in data_response:
                raise ValueError("JSON 缺少必需字段")
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
    async def call_llm(self, system_messages: List[SystemMessage], model_context: ChatCompletionContext , cancellation_token: CancellationToken) -> str:
        model_result = None
        async for inference_output in self._call_llm(
            model_client=self.model_client,
            model_client_stream=self.model_client_stream,
            system_messages=system_messages,
            model_context=model_context,
            agent_name=self.name,
            cancellation_token=cancellation_token,
            output_content_type=self._output_content_type,
        ):
            if isinstance(inference_output, CreateResult):
                model_result = inference_output
        assert model_result is not None, "No model result was produced."

        response_content = str(model_result.content).strip()
        # check response_content is null
        if not response_content:
            raise ValueError("Empty response from model")
        # clean response content
        cleaned_content = self._clean_response_content(response_content)
        # print(# debug
        #         f"Raw response: '{response_content}', Cleaned content: '{cleaned_content}'"
        #      )  
        return cleaned_content
    
    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        await self._model_context.clear()
        self._state = "planning" 

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
    print("run _electrical_docgen_agent.py")
    import asyncio

    asyncio.run(main())
