from autogen_agentchat.agents import BaseChatAgent
import os

from typing import (
    Any,
    AsyncGenerator,
    List,
    Optional,
    Sequence,
    Union,

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

    component_provider_override = "magentic_ui.users._electriacal_docgen_agent"

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
        这是一个专门用于电气设备需求文档信息完整性校验的AI助手。由中车株洲所，lamda实验室团队开发。
        它能够自动识别和提取轨道交通电气系统中的关键参数信息，并判断文档的完整性。

        ## 核心能力
        - **信息提取**: 从技术文档中自动识别关键电气参数
        - **完整性校验**: 判断必填字段是否完整
        - **格式标准化**: 输出统一的JSON格式结果
        - **智能提示**: 为缺失字段生成具体的补充要求

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
        output_content_type: type[BaseModel] | None = None,
        output_content_type_format: str | None = None,
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

        # Add the messages to the model context.
        await self._add_messages_to_context(
            model_context=self._model_context,
            messages=messages,
        )
        inner_messages: List[BaseAgentEvent | BaseChatMessage] = []
        logger.debug("Enter ElectricalRequirementValidator")
        
        # first step: jugement is contain all requirement message
        retry_count = 0
        validation_lsit = [
                        "complete",
                        "message",
                        "项目名称",
                        "牵引变流器-全动车变流参数-额定输出容量",
                        "牵引变流器-半动车变流参数-额定输出容量",
                        "辅助变流器-额定输入电压",
                        "辅助变流器-每列车数量",
                        "牵引变流器寿命",
                        ]
        while retry_count < self.max_retries:
            try:
                # 调用大模型进行信息判断与提取
                cleaned_content = await self.call_llm(
                    system_messages = [SystemMessage(content=ELECTRICAL_REQUIREMENT_VALIDATOR_PROMPT)], 
                    model_context = self._model_context, 
                    cancellation_token = cancellation_token
                )
                # 读取json
                self.data_response_planning = json.loads(str(cleaned_content))
                # json格式验证
                self._validation_json(validation_lsit, self.data_response_planning)
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
                    yield Response(chat_message=TextMessage(content = self.data_response_planning["message"], source=self.name), inner_messages=[], )
                    return
                elif self.data_response_planning["complete"] == True:  # generate data_response successful
                    # 解析并过滤字段
                    
                    filtered_data = {k: v for k, v in self.data_response_planning.items() if k not in ['complete', 'message']}

                    # 保存文件
                    file_name = '电气设计需求.json'
                    with open(self._work_root / self._work_relative_dir / file_name, 'w', encoding='utf-8') as f:
                        json.dump(filtered_data, f, ensure_ascii=False, indent=2) 
                    response_text = f"提取的字段为：{str(filtered_data)}, json 格式保存在 {self._work_root / self._work_relative_dir} 文件夹下的 {file_name} 文件中。"
                    yield Response(chat_message=TextMessage(content = response_text, source=self.name, ), inner_messages=[], )
                    return 
                else:
                    pass  
            except Exception as e:
                retry_count += 1
                logger.info(f"Error (尝试 {retry_count}/{self.max_retries}): {e}")
                if retry_count >= self.max_retries:
                    logger.info("达到最大重试次数")
                    # default value
                    break
                
                else:
                    continue 
        yield Response(chat_message=TextMessage(content = "达到最大重试次数", source=self.name, ), inner_messages=[], )    

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
            output_content_type=None,
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
        #logger.info(# debug
        # f"Raw response: '{response_content}', Cleaned content: '{cleaned_content}'"
        #)  
        return cleaned_content
    
    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        await self._model_context.clear()
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
        [electrical_requirement_validator, critic_agent], termination_condition=text_termination
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
       
"""
# 牵引变流器寿命100年
# 宁波至慈溪市域动车组项目
# 牵引变流器采购技术规范
# 每列车数量	4		
    await Console(team.run_stream(task=task))
    


if __name__ == "__main__":
    logger.info("run _electrical_docgen_agent.py")
    import asyncio

    asyncio.run(main())
