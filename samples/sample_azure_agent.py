import asyncio
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_ext.agents.azure._azure_ai_agent import AzureAIAgent
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
import dotenv
import os
from typing import Union, Any, Dict, Optional
from autogen_core.models import ChatCompletionClient
from autogen_core import ComponentModel
from autogen_agentchat.agents import UserProxyAgent

from magentic_ui.tools.playwright.browser import get_browser_resource_config
from magentic_ui.utils import get_internal_urls
from magentic_ui.teams import GroupChat, RoundRobinGroupChat
from magentic_ui.teams.orchestrator.orchestrator_config import OrchestratorConfig
from magentic_ui.agents import WebSurfer, CoderAgent, USER_PROXY_DESCRIPTION, FileSurfer
from magentic_ui.magentic_ui_config import MagenticUIConfig, ModelClientConfigs
from magentic_ui.types import RunPaths
from magentic_ui.agents.web_surfer import WebSurferConfig
from magentic_ui.agents.users import DummyUserProxy, MetadataUserProxy
from magentic_ui.approval_guard import (
    ApprovalGuard,
    ApprovalGuardContext,
    ApprovalConfig,
    BaseApprovalGuard,
)
from magentic_ui.input_func import InputFuncType, make_agentchat_input_func
from magentic_ui.learning.memory_provider import MemoryControllerProvider


async def azure_agent_example():
    """
    This is a simple example of how to use the AzureAIAgent.
    You can create any agent in the Azure AI Foundry and add it to the Magentic-UI team.
    """
    credential = DefaultAzureCredential()

    async with AIProjectClient.from_connection_string(  # type: ignore
        credential=credential, conn_str=os.getenv("AI_PROJECT_CONNECTION_STRING", "")
    ) as project_client:
        azure_agent = AzureAIAgent(
            name="azure_agent",
            description="An AI assistant",
            project_client=project_client,
            deployment_name="gpt-4o",  # EDIT TO YOUR DEPLOYMENT NAME
            instructions="You are a helpful assistant.",
            metadata={"source": "AzureAIAgent"},
        )

        result = await azure_agent.on_messages(
            messages=[
                TextMessage(
                    content="How are you doing?.",
                    source="user",
                )
            ],
            cancellation_token=CancellationToken(),
            message_limit=5,
        )
        print(result)


async def get_task_team_with_azure_agent(
    magentic_ui_config: Optional[MagenticUIConfig] = None,
    input_func: Optional[InputFuncType] = None,
    *,
    paths: RunPaths,
) -> GroupChat | RoundRobinGroupChat:
    """
    Creates and returns a GroupChat team with specified configuration.
    This sample shows how to add an Azure AI Foundry agent to the team.

    Args:
        magentic_ui_config (MagenticUIConfig, optional): Magentic UI configuration for team. Default: None.
        paths (RunPaths): Paths for internal and external run directories.

    Returns:
        GroupChat | RoundRobinGroupChat: An instance of GroupChat or RoundRobinGroupChat with the specified agents and configuration.
    """
    if magentic_ui_config is None:
        magentic_ui_config = MagenticUIConfig()

    def get_model_client(
        model_client_config: Union[ComponentModel, Dict[str, Any], None],
    ) -> ChatCompletionClient:
        if model_client_config is None:
            return ChatCompletionClient.load_component(
                ModelClientConfigs.get_default_client_config()
            )
        return ChatCompletionClient.load_component(model_client_config)

    if not magentic_ui_config.inside_docker:
        assert (
            paths.external_run_dir == paths.internal_run_dir
        ), "External and internal run dirs must be the same in non-docker mode"

    model_client_orch = get_model_client(
        magentic_ui_config.model_client_configs.orchestrator
    )
    approval_guard: BaseApprovalGuard | None = None

    approval_policy = (
        magentic_ui_config.approval_policy
        if magentic_ui_config.approval_policy
        else "never"
    )

    websurfer_loop_team: bool = (
        magentic_ui_config.websurfer_loop if magentic_ui_config else False
    )

    model_client_coder = get_model_client(magentic_ui_config.model_client_configs.coder)
    model_client_file_surfer = get_model_client(
        magentic_ui_config.model_client_configs.file_surfer
    )
    browser_resource_config, _novnc_port, _playwright_port = (
        get_browser_resource_config(
            paths.external_run_dir,
            magentic_ui_config.novnc_port,
            magentic_ui_config.playwright_port,
            magentic_ui_config.inside_docker,
            headless=magentic_ui_config.browser_headless,
            local=magentic_ui_config.browser_local,
        )
    )

    orchestrator_config = OrchestratorConfig(
        cooperative_planning=magentic_ui_config.cooperative_planning,
        autonomous_execution=magentic_ui_config.autonomous_execution,
        allowed_websites=magentic_ui_config.allowed_websites,
        plan=magentic_ui_config.plan,
        model_context_token_limit=magentic_ui_config.model_context_token_limit,
        do_bing_search=magentic_ui_config.do_bing_search,
        retrieve_relevant_plans=magentic_ui_config.retrieve_relevant_plans,
        memory_controller_key=magentic_ui_config.memory_controller_key,
        allow_follow_up_input=magentic_ui_config.allow_follow_up_input,
        final_answer_prompt=magentic_ui_config.final_answer_prompt,
    )
    websurfer_model_client = magentic_ui_config.model_client_configs.web_surfer
    if websurfer_model_client is None:
        websurfer_model_client = ModelClientConfigs.get_default_client_config()
    websurfer_config = WebSurferConfig(
        name="web_surfer",
        model_client=websurfer_model_client,
        browser=browser_resource_config,
        single_tab_mode=False,
        max_actions_per_step=magentic_ui_config.max_actions_per_step,
        url_statuses={key: "allowed" for key in orchestrator_config.allowed_websites}
        if orchestrator_config.allowed_websites
        else None,
        url_block_list=get_internal_urls(magentic_ui_config.inside_docker, paths),
        multiple_tools_per_call=magentic_ui_config.multiple_tools_per_call,
        downloads_folder=str(paths.internal_run_dir),
        debug_dir=str(paths.internal_run_dir),
        animate_actions=True,
        start_page=None,
        use_action_guard=True,
        to_save_screenshots=False,
    )

    user_proxy: DummyUserProxy | MetadataUserProxy | UserProxyAgent

    if magentic_ui_config.user_proxy_type == "dummy":
        user_proxy = DummyUserProxy(name="user_proxy")
    elif magentic_ui_config.user_proxy_type == "metadata":
        assert (
            magentic_ui_config.task is not None
        ), "Task must be provided for metadata user proxy"
        assert (
            magentic_ui_config.hints is not None
        ), "Hints must be provided for metadata user proxy"
        assert (
            magentic_ui_config.answer is not None
        ), "Answer must be provided for metadata user proxy"
        user_proxy = MetadataUserProxy(
            name="user_proxy",
            description="Metadata User Proxy Agent",
            task=magentic_ui_config.task,
            helpful_task_hints=magentic_ui_config.hints,
            task_answer=magentic_ui_config.answer,
            model_client=model_client_orch,
        )
    else:
        user_proxy_input_func = make_agentchat_input_func(input_func)
        user_proxy = UserProxyAgent(
            description=USER_PROXY_DESCRIPTION,
            name="user_proxy",
            input_func=user_proxy_input_func,
        )

    if magentic_ui_config.user_proxy_type in ["dummy", "metadata"]:
        model_client_action_guard = get_model_client(
            magentic_ui_config.model_client_configs.action_guard
        )

        # Simple approval function that always returns yes
        def always_yes_input(prompt: str, input_type: str = "text_input") -> str:
            return "yes"

        approval_guard = ApprovalGuard(
            input_func=always_yes_input,
            default_approval=False,
            model_client=model_client_action_guard,
            config=ApprovalConfig(
                approval_policy=approval_policy,
            ),
        )
    elif input_func is not None:
        model_client_action_guard = get_model_client(
            magentic_ui_config.model_client_configs.action_guard
        )
        approval_guard = ApprovalGuard(
            input_func=input_func,
            default_approval=False,
            model_client=model_client_action_guard,
            config=ApprovalConfig(
                approval_policy=approval_policy,
            ),
        )
    with ApprovalGuardContext.populate_context(approval_guard):
        web_surfer = WebSurfer.from_config(websurfer_config)
    if websurfer_loop_team:
        # simplified team of only the web surfer
        team = RoundRobinGroupChat(
            participants=[web_surfer, user_proxy],
            max_turns=10000,
        )
        await team.lazy_init()
        return team

    coder_agent = CoderAgent(
        name="coder_agent",
        model_client=model_client_coder,
        work_dir=paths.internal_run_dir,
        bind_dir=paths.external_run_dir,
        model_context_token_limit=magentic_ui_config.model_context_token_limit,
        approval_guard=approval_guard,
    )

    file_surfer = FileSurfer(
        name="file_surfer",
        model_client=model_client_file_surfer,
        work_dir=paths.internal_run_dir,
        bind_dir=paths.external_run_dir,
        model_context_token_limit=magentic_ui_config.model_context_token_limit,
        approval_guard=approval_guard,
    )

    if (
        orchestrator_config.memory_controller_key is not None
        and orchestrator_config.retrieve_relevant_plans in ["reuse", "hint"]
    ):
        memory_provider = MemoryControllerProvider(
            internal_workspace_root=paths.internal_root_dir,
            external_workspace_root=paths.external_root_dir,
            inside_docker=magentic_ui_config.inside_docker,
        )
    else:
        memory_provider = None
    credential = DefaultAzureCredential()

    async with AIProjectClient.from_connection_string(  # type: ignore
        credential=credential, conn_str=os.getenv("AI_PROJECT_CONNECTION_STRING", "")
    ) as project_client:
        azure_reasoning_agent = AzureAIAgent(
            name="azure_reasoning_agent",
            description="An AI assistant that can help with complex math and logic tasks",
            project_client=project_client,
            deployment_name="o3-mini",  # EDIT TO YOUR DEPLOYMENT NAME
            instructions="You are a helpful assistant.",
            metadata={"source": "AzureAIAgent"},
        )

        team = GroupChat(
            name="azure_agent_team",
            description="A team of agents that can help with complex math and logic tasks",
            participants=[
                web_surfer,
                user_proxy,
                coder_agent,
                file_surfer,
                azure_reasoning_agent,
            ],
            orchestrator_config=orchestrator_config,
            model_client=model_client_orch,
            memory_provider=memory_provider,
        )

        await team.lazy_init()
        return team


if __name__ == "__main__":
    dotenv.load_dotenv()
    asyncio.run(azure_agent_example())
