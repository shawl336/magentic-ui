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
          "id": "1",
          "title": "电气设计需求提取与分析",
          "details": "提取和整理用户电气设计的需求为后续的电气设计步骤提供必要的上下文信息。",
          "agent_name": "electrical_requirement_validator",
          "additional_description": "输入指令应该包含用户的原始设计请求以及设计需求文档的地址(如果提供了)。用户的原始设计请求可以是宽泛的电气设计请求，比如:\\"设计一个某某变流器\\"，\\"我要做变流器设计\\"等，该智能体会引导用户补全必须的需求信息。不会直接输出需求内容，而是以文档的形式保存在一个路径。"
        },
        {
          "id": "2",
          "title": "生成技术规格说明书",
          "details": "基于第1步整理好的需求文档，生成技术规格书并归档。",
          "agent_name": "electrialcal_technical_specification_generator",
          "additional_description": "输入指令应该包含第1步整理好的需求文档。输出技术规格说明书文档，不直接输出技术规格说明书内容，而是以文档的形式保存在一个路径，路径需要传送至后续步骤的输入。"
        },
        {
          "id": "3",
          "title": "电路图生成",
          "details": "基于第1步整理好的需求文档，生成对应的电路拓扑图、电路描述。",
          "agent_name": "electrical_design_agent",
          "additional_description": "输入指令应该包含第1步整理好的需求文档，迭代地与用户进行电路拓扑图的生成和修改直到用户确认生成的电路图符合要求。输出包含电路描述和电路拓扑图的文件路径。图片格式的电路用来预览审阅，除了图片格式的电路拓扑图还会生成CAD软件可以查看编辑的CAD格式电路文件。"
        },
        {
          "id": "4",
          "title": "生成方案设计说明书",
          "details": "基于第3步的电路图，生成方案设计说明书。",
          "agent_name": "electrialcal_project_design_generator",
          "additional_description": "输入指令应该包含第3步整理好的电路拓扑图、电路描述。输出技术方案设计说明书，不直接输出暗杆设计说明书的内容，而是以文档的形式保存在一个路径。"
        },
        {
          "id": "5",
          "title": "物料选型与仿真验证",
          "details": "基于第2步的技术规格说明书和第3步生成的电气参数（电路拓扑图和电路描述），选择合适的物料，生成符合需求参数要求的电气物料。并进行仿真验证，确保生成的电气设计满足需求。",
          "agent_name": "material_selection_agent",
          "additional_description": "输入是第2步生成的<技术规格说明书>文件路径和第3步生成的<电路拓扑图>的文件路径和第3步中的电路描述。输出选型得到的物料参数列表，以及物料清单文件的保存路径。并进行仿真验证，确保生成的电气设计满足需求。"
        },
        {
          "id": "6",
          "title": "打开 Creo 软件",
          "details": "打开 Creo 软件，等待用户通知仿真任务完成。",
          "agent_name": "open_creo_agent",
          "additional_description": "输入是第5步生成的<物料清单>文件路径。输出打开 Creo 软件的命令。"
        },
    ]
  },
  "文档生成工作计划": {
    "description": "文档生成工作计划对应的是变压器，变流器等电气设备的相关文档生成。包含了电气设计需求提取与分析，文档生成等步骤。",
    "steps": [
        {
          "id": "1",
          "title": "电气设计需求提取与分析",
          "details": "提取和整理用户电气设计的需求为后续的电气设计步骤提供必要的上下文信息。",
          "agent_name": "electrical_requirement_validator",
          "additional_description": "这个一步的输入是用户的原始请求。不要包含任何其他信息，也不要用户补充任何信息，请直接输入用户的原始请求！"
        },
        {
          "id": "2",
          "title": "生成技术规格说明书",
          "details": "基于第1步整理好的需求文档，生成技术规格书并归档。",
          "agent_name": "documentation_analysis_and_generation_agent",
          "additional_description": "输入指令应该包含第1步整理好的需求文档。输出技术规格说明书文档，不直接输出技术规格说明书内容，而是以文档的形式保存在一个路径。"
        },

    ]
  }
}
"""

# PRESET_PLANS: str = """
# {
#   "电气设计工作计划": {
#     "description": "电气设计工作计划对应的是变压器，变流器等电气设备的设计。包含了电气设计需求提取与分析，电路图生成，人工审查电路图，计算电气参数，电气物料选型，人工仿真等步骤。",
#     "steps": [
#         {
#           "id": "1"
#           "title": "电气设计需求提取与分析",
#           "details": "提取和整理用户电气设计的需求为后续的电气设计步骤提供必要的上下文信息。",
#           "agent_name": "electrical_requirement_validator",
#           "additional_description": "输入指令应该包含用户的原始设计请求以及设计需求文档的地址(如果提供了)。用户的原始设计请求可以是宽泛的电气设计请求，比如:"设计一个某某变流器"，"我要做变流器设计"等，该智能体会引导用户补全必须的需求信息。
#             不会直接输出需求内容，而是以文档的形式保存在一个路径。"
#         },
#         {
#           "id": "2"
#           "title": "生成技术规格说明书",
#           "details": "基于第1步整理好的需求文档，生成技术规格书并归档。",
#           "agent_name": "documentation_analysis_and_generation_agent",
#           "additional_description": "输入指令应该包含第1步整理好的需求文档。输出技术规格说明书文档，不直接输出技术规格说明书内容，而是以文档的形式保存在一个路径。"
#         },
#         {
#           "id": "3"
#           "title": "电路图生成",
#           "details": "基于第1步整理好的需求文档，生成对应的电路拓扑图、电路描述。",
#           "agent_name": "electrical_design_agent",
#           "additional_description": "输入指令应该包含第1步整理好的需求文档，迭代地与用户进行电路拓扑图的生成和修改直到用户确认生成的电路图符合要求。输出包含电路描述和电路拓扑图的文件路径。图片格式的电路用来预览审阅，除了图片格式的电路拓扑图还会生成CAD软件可以查看编辑的CAD格式电路文件。"
#         },
#         {
#           "id": "4"
#           "title": "电气物料选型",
#           "details": "基于第3步生成的电气参数，选择合适的物料，生成符合需求参数要求的电气物料。",
#           "agent_name": "user_proxy",
#           "additional_description": "输入是第2步生成的<技术规格说明书>文件路径和第3步生成的<电路拓扑图>的文件路径。输出选型得到的物料参数列表，以及物料清单文件的保存路径"。
#         },
#         {
#           "id": "5"
#           "title": "人工仿真",
#           "details": "将之前各个步骤选型的物料参数和电路拓扑图(CAD格式)发送给用户，打开仿真工具，等待用户通知仿真任务完成。",
#           "agent_name": "user_proxy",
#           "additional_description": ""
#     ]
#   },
# }
# """

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