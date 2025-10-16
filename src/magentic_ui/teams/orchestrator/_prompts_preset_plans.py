"""
Workflows must contain a `description` and `steps`.
Steps must contain a `id`, `title`, `details`, and an `agent_name`.
If an agent_name is not provided, the step will be executed by the default agent, otherwise the orchestrator will try to delegate an agent from the team.
"""

PRESET_PLANS: str = """
{
  "电气设计工作计划": {
    "description": "电气设计工作计划对应的是变压器，变流器等电气设备的设计。包含了电气设计需求提取与分析，电路图生成，人工审查电路图，计算电气参数，电气物料选型，人工仿真等步骤。",
    "steps": [
        {
          "id": "1"
          "title": "电气设计需求提取与分析",
          "details": "提取和整理用户电气设计的需求为后续的电气设计步骤提供必要的上下文信息。输入是用户的直接需求或者需求文档。",
          "agent_name": "electrical_requirement_validator",
          "additional_description": "当前步骤同样接受用户宽泛的电气设计请求，比如:"设计一个某某变流器"，"我要做变流器设计"等，智能体会引导用户补全必须的需求信息。
            不直接输出需求内容，而是以文档的形式保存在一个路径。"
        },
        {
          "id": "2"
          "title": "电路图生成",
          "details": "基于第1步整理好的需求文档，生成对应的电路拓扑图以及电路描述。输入是需求文档。",
          "agent_name": "electrical_design_agent",
          "additional_description": "不直接输出电路图内容，而是以文档的形式保存在一个路径。"
        },
        {
          "id": "3"
          "title": "人工审查电路图",
          "details": "将第2步生成的电路图和电路描述发送给用户，打开的编辑审阅工具让用户进行审查和修改，等待用户通知审查完成，最后更新并保存用户的审查修改后的电路图。输入是电路拓扑图以及电路描述。",
          "agent_name": "user_proxy",
          "additional_description": ""
        },
        {
          "id": "3"
          "title": "计算电气参数",
          "details": "计算电路图的电气参数，用以帮助电气物料选型",
          "agent_name": "user_proxy",
          "additional_description": ""
        },
        {
          "id": "4"
          "title": "电气物料选型",
          "details": "基于第3步生成的电气参数，选择合适的物料。输入是电气参数计算结果，输出符合参数区间的电气物料表格，同时以文档的形式保存在一个路径。",
          "agent_name": "user_proxy",
          "additional_description": ""
        },
        {
          "id": "5"
          "title": "人工仿真",
          "details": "将之前各个步骤生成的物料参数发送给用户，打开仿真工具进行仿真，等待用户通知仿真任务完成。",
          "agent_name": "user_proxy",
          "additional_description": ""
    ]
  },
}
"""

# PRESET_PLANS: str = """
# {
#   "电气设计工作计划": {
#     "description": "电气设计工作计划对应的是变压器，变流器等电气设备的设计。包含了电气设计需求提取与分析，电路图生成，人工审查电路图，计算电气参数，电气物料选型，人工仿真等步骤。",
#     "steps": [
#         {
#           "title": "电气设计需求提取与分析",
#           "details": "提取和分析用户的电气设计需求，生成电气设计所必要的完整需求文档",
#           "agent_name": "electrical_requirement_validator",
#         },
#         {
#           "title": "电路图生成",
#           "details": "生成满足需求的电路图以及电路描述",
#           "agent_name": "electrical_design_agent",
#         },
#         {
#           "title": "人工审查电路图",
#           "details": "交给用户人工审查电路图和电路描述，确保满足需求，等待用户确认后完成",
#           "agent_name": "user_cad_agent",
#         },
#         {
#           "title": "计算电气参数",
#           "details": "计算电路图的电气参数，用以帮助电气物料选型",
#           "agent_name": "use_cad_agent",
#         },
#         {
#           "title": "电气物料选型",
#           "details": "选择满足需求，并符合电气参数要求的电气物料",
#           "agent_name": "bom_selection_agent",
#         },
#         {
#           "title": "人工仿真",
#           "details": "交给用户进行人工仿真，确保生成的电气设计满足需求，等待用户确认后完成",
#           "agent_name": "user_simulink_agent",
#         }
#     ]
#   },
# }
# """