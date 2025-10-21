from autogen_agentchat.agents import BaseChatAgent
import os
import aiofiles

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
        ) = """
        你是电气设备需求文档智能校验助手，专门负责轨道交通电气系统需求文档的信息提取与完整性验证。
        专注于轨道交通电气设备需求文档的完整性判断与信息提取，服务于项目需求评审和技术规格验证。
        请严格按照电气参数识别规则处理输入文档，输出标准化的JSON验证结果。
        """,
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
        self._system_messages: List[SystemMessage] = []
        if system_message is None:
            self._system_messages = []
        else:
            self._system_messages = [SystemMessage(content=system_message)]

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
            system_prompt=ELECTRICAL_REQUIREMENT_VALIDATOR_PROMPT
        )

        validation_list = [
            "complete",
            "message",
            "文档类型",
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

    task = """
1概述
1.1用途
本技术规范描述了宁波至慈溪市域动车组项目牵引变流器技术要求，同时也是其订货技术条件。
1.2基本信息
见《宁波至慈溪市域动车组项目通用技术规范》1.2。
1.3通用标准
通用标准应满足《宁波至慈溪市域动车组项目通用技术规范》1.3的要求。
1.4气候条件和运用海拔
见《宁波至慈溪市域动车组项目通用技术规范》1.4。
2部件环境条件
2.1相对湿度、海拔、气压
见《宁波至慈溪市域动车组项目通用技术规范》2.1。
2.2温度
见《宁波至慈溪市域动车组项目通用技术规范》2.2。
3部件技术要求
3.1概述
卖方对牵引变流器的设计、产品的性能、可靠性、有关法律要求等各方面负全部责任，并满足买方的要求。
株机公司对于任何明示或默示的条件、陈述及担保，包括对适销性、特定用途适用性或非侵权性的任何默示的担保，均不予负责，遵循此技术规范，并不能免除卖方的任何法定责任。
此文件对牵引变流器及箱体的卖方及其供应商有效，此技术规范视同卖方的合同的一部分。牵引变流器及箱体的设计和结构应遵循所有规定的条款、与最终用户签订的会议纪要、与最终用户签订合同中与卖方供货部件相关的技术条款、买卖双方签订的会议纪要和相关国内外标准。
若本技术规范中有相抵触或歧异的条款，卖方应向买方申请澄清该条款，否则以买方解释的为准。文件中采用的标准均为最新年代的标准。
卖方不应把买方提供的文件、图纸、三维图形等资料泄露给第三方，一经发现将追究法律责任及赔偿买方的经济损失。
凡在设计、制造过程中，如涉及本规范中未规定的内容，应符合有关标准和规定，并提交买方确认后执行。牵引变流器及箱体方案双方确定后，卖方对其的任何更改应及时通知买方，得到签字确认后方可实施。未经买方的认可，卖方对业主的任何口头或/和书面承诺，应都视为免费提供。技术合同的最终解释权归买方，买方的确认签字，并不能作为排除卖方责任的依据。
若发现本技术规范的任何规定不适用，文件的其它规定仍然有效。同样，如果确定（必须遵照的）任何规范不适用，其它规范仍然有用。
作为供货范围的一部分，卖方必须按照第6章中规定的格式提交附有日期和地址的完整文档。卖方供货范围及服务包括以下方面，见下表。 
3.3.3基本参数
牵引变流器主要技术参数
项  目	全动车变流参数	半动车变流参数	备  注
额定输出容量	4*256kVA	457kVA	
额定效率	≥67.5％	≥56.5％	
功率因数	≥0.28		
总谐波含量	＜20%		输入侧
四象限（4QC）	
3.3.6主变流器风机参数
风机参数见下表。
变流器风机基本参数
项目	参数	备注
输入额定电压	3AC 380V	
轴功率	2.5kW	
效率	0.841	
功率因数	0.86(电机)	
输入功率	3kW（单台风机）	半动车变流器内部有2台风机，全动车变流器内部1台风机
额定电流	6.3A	
启动电流	54A	
3.3.7接地要求
每台变流器箱体与车体底架之间用软编织线连接，沿动车组纵向的变流器箱体外侧各带有2个M10接地螺母座，对称布置。
3.3.8安全接地要求
在打开变流器柜门时，能够实现变流器自身接地。变流器内部的所有金属部位均应该与变流器箱体进行可靠安全接地，同时变流器柜门均应与变流器箱体进行可靠安全接地。
在方案设计阶段，株机公司提供钥匙联锁方案给供应商，供应商提供相应的钥匙方案给株机公司确认。
3.3.9辅助变流器
3.3.9.1基本参数
辅助变流器参数见下表。
辅助变流器参数表
项目	参数	备注
供电方式	主变流器直流环节	
	
额定输入电压	DC 2500V	
额定三相输出电压	5×AC420Vrms	三相四线制
稳态输出电压允差	± 8 %	额定负载
额定单相输出电压	420Vrms±5%	
输出额定频率	78Hz	
- 列车最大运行速度： 160km/h
- 列车最大结构速度： 80km/h
- 列车平均初始加速度： (0—120km/h)≥0.5m/s_2
- 列车平均加速度： (0—120km/h)≥0.5m/s_2
- 列车平均旅行速度： 100km/h
    
"""
    # - 编组规格： 4M2T
    # - 重量要求： 全动车牵引变流器最大重量<= 1400KG ，偏差-2% - 0%
    await Console(team.run_stream(task=task))


if __name__ == "__main__":
    logger.info("run _electrical_docgen_agent.py")
    import asyncio

    asyncio.run(main())
