"""
Workflows must contain a `description` and `steps`.
Steps must contain a `title`, `details`, and an optional `agent_name`.
If an agent_name is not provided, the step will be executed by the default agent, otherwise the orchestrator will try to delegate an agent from the team.
"""
PRESET_TASKS: str = """
{
  "电气设计工作流程": {
    "description": "电气设计工作流程对应的是变压器，变流器等电气设备的设计。包含了电气设计需求提取与分析，电路图生成，人工审查电路图，计算电气参数，电气物料选型，人工仿真等步骤。",
    "steps": [
        {
          "title": "电气设计需求提取与分析",
          "details": "提取和分析用户的电气设计需求，生成电气设计所必要的完整需求文档",
          "agent_name": "documentation_analysis_and_generation_agent",
        },
        {
          "title": "电路图生成",
          "details": "生成满足需求的电路图以及电路描述",
          "agent_name": "electrical_design_agent",
        },
        {
          "title": "人工审查电路图",
          "details": "人工审查电路图和电路描述，确保满足需求",
          "agent_name": "user_cad_agent",
        },
        {
          "title": "计算电气参数",
          "details": "计算电路图的电气参数，用以帮助电气物料选型",
          "agent_name": "use_cad_agent",
        },
        {
          "title": "电气物料选型",
          "details": "选择满足需求，并符合电气参数要求的电气物料",
          "agent_name": "bom_selection_agent",
        },
        {
          "title": "人工仿真",
          "details": "进行人工仿真，确保生成的电气设计满足需求",
          "agent_name": "user_simulink_agent",
        }
    ]
  },
}
"""