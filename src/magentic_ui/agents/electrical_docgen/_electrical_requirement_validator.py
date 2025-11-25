from autogen_agentchat.agents import BaseChatAgent
import os
import aiofiles
import asyncio

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
from autogen_core import CancellationToken
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
    SystemMessage,
    UserMessage,
)
from autogen_agentchat.utils import remove_images
from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    TextMessage,
    HandoffMessage,
)

from magentic_ui.utils import thread_to_context
from magentic_ui.teams.orchestrator._utils import extract_json_from_string
from ._prompts import (
    ELECTRICAL_REQUIREMENT_VALIDATOR_PROMPT,
)
from pathlib import Path
import json
from loguru import logger


class ElectricalRequirementValidator(BaseChatAgent):
    """Electrical Documentation Generation Agent

    Core capabilities:
    ------------------
    - Generate documentation for electrical systems
    - Accept plain-language electrical design requirements
    usage example:
        electrialcal_docgen_agent = ElectricalRequirementValidator()
    """

    component_provider_override = "magentic_ui.users._electricalrequirementvalidator"

    def __init__(
        self,
        name: str,
        model_client: ChatCompletionClient,
        work_root: Path,
        work_relative_dir: Path,
        bind_root: Path,
        bind_relative_dir: Path,
        max_retries: int = 2,
        *,
        description: str = """
        ## 功能概述
        这是一个电气设计需求分析专家，具备丰富的电气设计经验，熟悉完成一个电气必须具备哪些需求参数。
        它接受用户的电气需求，检查必要需求的完整性并引导用户补充缺失的信息，自动识别和提取轨道交通电气系统设计需求中的关键参数信息。
        或者向它提出一个宽泛的电气设计请求，比如: "我要设计一个牵引变流器"，它将引导用户一步步补充必要的需求参数。

        ## 核心能力
        - **完整性校验**: 检查是否提供了全部的必要需求信息，若未提供则生成具体的补充要求
        - **引导需求信息补全**: 根据用户的请求类型，引导并提示用户提供必要的参数信息
        - **信息提取**: 从技术文档中自动识别关键电气参数
        - **格式标准化**: 输出统一的JSON格式结果

        ## 适用场景
        - 项目需求文档完整性判断
        - 项目需求文档信息提取
        """,
        system_message: (
            str | None
        ) = "",
        model_client_stream: bool = False,
    ):
        """
        Initialize the electrialcalDocGen agent.

        Args:
            name (str): The name of the agent.
        """
        super().__init__(name=name, description=description)
        self._work_root = work_root
        self._work_relative_dir = work_relative_dir
        self._bind_root = bind_root
        self._bind_relative_dir = bind_relative_dir
        self.max_retries = max_retries
        self.model_client = model_client
        self.model_client_stream = model_client_stream
        self.message_history: List[BaseChatMessage | BaseAgentEvent] = []
        # self._system_messages: List[SystemMessage] = []
        # if system_message is None:
        #     self._system_messages = []
        # else:
        #     self._system_messages = [SystemMessage(content=system_message)]

        self._model_context = UnboundedChatCompletionContext()

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

    async def on_messages_stream_foo(
        self
    ) -> Response:
        """
        For debug only, skip the token-consuming and time-consuming process
        """
        await asyncio.sleep(3)  # Sleep for 2 seconds

         # 保存文件
        filtered_data = { 
            "项目名称": "上海轨道交通市域线机场联络线牵引变流器项目", 
            "直流高压等级数值": "DC 1800V", 
            "列车最大运行速度": "160km/h", 
            "列车最大结构速度": "80km/h", 
            "列车平均初始加速度": "≥0.8m/s2（8 辆编组）、≥1.0m/s2（4 辆编组）", 
            "列车平均加速度": "≥0.38m/s2（8 辆编组）、≥0.45m/s2（4 辆编组）", 
            "列车平均旅行速度": "95km/h", 
            "编组规格": "4动4拖两种编组形式，8编组或者4编组", 
            "重量要求": "全动车牵引变流器最大重量<= 1400KG ，偏差-2% - 0%",
            "complete": True,
            "message": "需求提取已经全部完成",
        }
        
        logger.warning("In debug mode, using the foo on_messages_stream")          
        file_name = "电气设计需求.json"
        async with aiofiles.open(
            self._work_root / self._work_relative_dir / file_name,
            "w",
            encoding="utf-8",
        ) as f:
            json_str = json.dumps(filtered_data, ensure_ascii=False, indent=2)
            await f.write(json_str)
            
        response_text = f"需求提取已经全部完成，提取的字段为：{json.dumps(filtered_data, ensure_ascii=False, indent=4)}, json 格式保存在{file_name} 文件中。"
        return Response(
            chat_message=TextMessage(
                content=response_text,
                source=self.name,
            ),
            inner_messages=[],
        )
    
    async def on_messages_stream(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        """
        Process the incoming messages with the ElectrialcalDocGen agent and yield events/responses as they happen.
        """
        logger.debug("Enter ElectricalRequirementValidator")

        self.message_history.extend(messages)
        inner_messages: List[BaseAgentEvent | BaseChatMessage] = []
        # manage context messages
        context_messages = self._thread_to_context(
            system_prompt=ELECTRICAL_REQUIREMENT_VALIDATOR_PROMPT + "\n /no_think"
        )

        validation_list = [
            "complete",
            "message",
            "项目名称",
            "直流高压等级数值",
            "列车最大运行速度",
            "列车最大结构速度",
            "列车平均初始加速度",
            "列车平均加速度",
            "列车平均旅行速度",
            "编组规格",
            "重量要求",
        ]
        
        try:
            # DEBUG
            yield await self.on_messages_stream_foo()
            return
            
            # get json response result
            self._data_response = await self._get_json_response(
                context_messages,
                lambda data: self._validation_json(validation_list, data),
                cancellation_token,
            )

            # parse data_response
            if self._data_response["complete"] == False:
                # yeild response to manager
                yield Response(
                    chat_message=TextMessage(
                        content=self._data_response["message"], source=self.name, metadata={"to_user": "yes"}
                    ),
                    inner_messages=inner_messages,
                )
                return
            elif (
                self._data_response["complete"] == True
            ):  # generate data_response successful
                # 解析并过滤字段
                filtered_data = {
                    k: v
                    for k, v in self._data_response.items()
                    if k not in ["complete", "message"]
                }

                # 保存文件            
                file_name = "电气设计需求.json"
                async with aiofiles.open(
                    self._work_root / self._work_relative_dir / file_name,
                    "w",
                    encoding="utf-8",
                ) as f:
                    json_str = json.dumps(filtered_data, ensure_ascii=False, indent=2)
                    await f.write(json_str)
                    
                response_text = f"需求提取已经全部完成，提取的字段为：{json.dumps(filtered_data, ensure_ascii=False, indent=4)}, json 格式保存在{file_name} 文件中。"
                yield Response(
                    chat_message=TextMessage(
                        content=response_text,
                        source=self.name,
                    ),
                    inner_messages=[],
                )
                return
            else:
                logger.debug("Invalid _data_response value.")
                raise ValueError("无效的返回格式")
        except Exception as e:
            yield Response(
                chat_message=TextMessage(
                    content=f"电气需求提取失败: {e}",
                    source=self.name,
                ),
                inner_messages=[],
            )

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
                
                # async for chunk in self.model_client.create_stream(
                #     token_limited_messages,
                #     tools=[],
                #     json_output=(True if self.model_client.model_info["json_output"] else False),
                #     cancellation_token=cancellation_token,
                # ):
                #     if isinstance(chunk, CreateResult):
                #         response = chunk
                #     elif isinstance(chunk, str):
                #         print(chunk, flush=True, end="")
                #     else:
                #         raise RuntimeError(f"Invalid chunk type: {type(chunk)}")
                
                assert isinstance(response.content, str)

                try:
                    response.content = self._clean_response_content(response.content)
                    json_response = json.loads(response.content)
                    # Use the validate_json function to check the response
                    if validate_json(json_response):
                        return json_response
                    else:
                        exception_message = "JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象且必须包含所有要求的字段。"
                        logger.debug(
                            f"JSON响应的验证失败: {json_response}, 正在重试 ({retries}/{self.max_retries})"
                        )
                except json.JSONDecodeError as e:
                    json_response = extract_json_from_string(response.content)
                    if json_response:
                        if validate_json(json_response):
                            return json_response
                        else:
                            exception_message = "JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象且必须包含所有要求的字段。"
                    else:
                        exception_message = f"JSON响应的验证失败，正在重试。你必须从响应中返回一个有效JSON对象且必须包含所有要求的字段。 错误: {e}"
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
            logger.debug(f"ElectricalRequirementValidator遇到错误: {e}", internal=False)
            raise

    def _validation_json(
        self, project_designed_generate_list: list[str], data_response: dict[str, Any]
    ) -> bool:
        """验证JSON响应，返回验证结果"""
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

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        await self._model_context.clear()
        self.message_history = []

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

# import PyPDF2
# def extract_text_with_pypdf2(pdf_path:str) -> str:
#     text = ""
#     with open(pdf_path, 'rb') as file:
#         pdf_reader = PyPDF2.PdfReader(file)
#         num_pages = len(pdf_reader.pages)
#         for page_num in range(num_pages):
#             page = pdf_reader.pages[page_num]
#             text += page.extract_text()
#     return text
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
            "json_output": True,
            "family": ModelFamily.R1,
            "structured_output": True,
        },
    )
    current_file_path = __file__
    current_dir_os_path = os.path.dirname(os.path.abspath(current_file_path))
    electrical_requirement_validator = ElectricalRequirementValidator(
        "electrical_requirement_validator",
        model_client,
        work_root=Path(),
        work_relative_dir=Path(),
        bind_root=Path(),
        bind_relative_dir=Path(),
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
        [electrical_requirement_validator, critic_agent],
        termination_condition=text_termination,
    )
    # Use `asyncio.run(...)` when running in a script.
    from autogen_agentchat.ui import Console
    
    current_file_path = __file__
    current_dir_os_path = os.path.dirname(os.path.abspath(current_file_path))
    pdf_path = os.path.join(current_dir_os_path, "docx_template/Sign_RD0001414931_上海轨道交通市域线机场联络线工程车辆采购项目牵引系统采购技术条件20221115.pdf")
    # extracted_text = extract_text_with_pypdf2(pdf_path).strip("\n")#[10000:20000]
    # print(extracted_text)
    task = "extracted_text"
    
#     task = """
# 上海机场联络线市域动车组为动力分散式单层电动车组，轴重≯17t，采用 3 动 1 拖及
# 4 动 4 拖两种编组形式 
# 额定直流电压1800V
# 列车试验速度：≥176km/h
# 平均启动加速度（0～40km/h）：≥0.8m/s2（8 辆编组）、≥1.0m/s2（4 辆编组）
# 平均加速度（0～160km/h）：≥0.38m/s2（8 辆编组）、≥0.45m/s2（4 辆编组）
# 列车最高运行速度：≥160km/h
# 直流
# 环节
# PWMI
# 效率
# 冷却
# 额定输入频率 50 Hz
# 额定开关频率 450Hz
# 额定直流电压 1800V
# 5 重量管理
# 重量目标值是XXXkg， 偏差范围±X%（一般规定为-X%，+X%）。
# 卖方必须使用附件18中05-1和05-2号文件模板的表格形式来制作并提交重量数据和重
# 心数据的文件。
# 每列车每年的运行里数300000 公里 (暂定)
# 列车平均旅行速度95km/h
# 每列车平均每天运营时间18 小时 (暂定)
# 设计寿命30 年
# 人工成本每人每小时人民币 40 元
# 运行条件AW2 载荷
# """
    # - 编组规格： 4M2T
    # - 重量要求： 全动车牵引变流器最大重量<= 1400KG ，偏差-2% - 0%
    await Console(team.run_stream(task=task))


if __name__ == "__main__":
    logger.info("run _electrical_docgen_agent.py")
    import asyncio

    asyncio.run(main())
