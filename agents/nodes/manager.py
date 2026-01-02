"""代码审查工作流的 Manager 节点。

接收文件分析结果，生成专家任务列表，并按风险类型分组以支持并行执行。
使用 LCEL 语法和 PydanticOutputParser。
"""

import logging
from typing import Dict, Any, List
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.language_models import BaseChatModel
from core.state import ReviewState, RiskItem, RiskType, WorkListResponse
from agents.prompts import render_prompt_template
from collections import defaultdict

logger = logging.getLogger(__name__)


async def manager_node(state: ReviewState) -> Dict[str, Any]:
    """Manager 节点：生成任务列表并按风险类型分组。
    
    Returns:
        包含 'work_list' 和 'expert_tasks' 键的字典。
    """
    print("\n" + "="*80)
    print("👔 [节点2] Manager - 生成任务列表并分组")
    print("="*80)
    
    # 获取 LLM（从 metadata）
    llm: BaseChatModel = state.get("metadata", {}).get("llm")
    if not llm:
        logger.error("LLM not found in metadata")
        return {"work_list": [], "expert_tasks": {}}
    
    file_analyses_dicts = state.get("file_analyses", [])
    diff_context = state.get("diff_context", "")
    
    if not file_analyses_dicts:
        print("  ⚠️  没有文件分析结果")
        logger.warning("No file analyses available for manager")
        return {"work_list": [], "expert_tasks": {}}
    
    # Convert dicts to Pydantic models for processing
    from core.state import FileAnalysis
    file_analyses = [FileAnalysis(**fa) if isinstance(fa, dict) else fa for fa in file_analyses_dicts]
    
    print(f"  📥 接收文件分析: {len(file_analyses)} 个")
    
    try:
        work_list = []
        grouped = defaultdict(list)
        for file_analyse in file_analyses:
            for w in file_analyse.potential_risks:
                key = (w.file_path, w.risk_type, w.line_number)
                grouped[key].append(w)

        for key, works in grouped.items():
            file_path, risk_type, line_number = key
            descriptions = [w.description for w in works]
            merged_description = "\n".join(descriptions)
            confidence = sum(w.confidence for w in works) / len(works)

            risk_item = RiskItem(
                risk_type=risk_type,
                file_path=file_path,
                line_number=line_number,
                description=merged_description,
                confidence=confidence
                # severity 和 suggestion 使用默认值
            )
            work_list.append(risk_item)


        # Convert lint_errors to RiskItems and add to work_list
        lint_errors = state.get("lint_errors", [])
        if lint_errors:
            lint_risk_items = _convert_lint_errors_to_risk_items(lint_errors)
            work_list.extend(lint_risk_items)
            print(f"  📋 添加语法分析任务: {len(lint_risk_items)} 个")
        
        # Group work_list by risk_type
        expert_tasks = _group_tasks_by_risk_type(work_list)

        print(f"  ✅ worklist ")

        print(f"  ✅ Manager 完成!")
        print(f"     - 生成任务数: {len(work_list)}")
        print(f"     - 专家组数量: {len(expert_tasks)}")
        print(f"     - 任务分组:")
        for risk_type, tasks in expert_tasks.items():
            print(f"       • {risk_type}: {len(tasks)} 个任务")
        print("="*80)
        logger.info(f"Manager generated {len(work_list)} tasks, grouped into {len(expert_tasks)} expert groups")
        
        # Convert Pydantic models to dicts for state (LangGraph TypedDict compatibility)
        work_list_dicts = [item.model_dump() for item in work_list]
        expert_tasks_dicts = {
            risk_type: [item.model_dump() for item in items]
            for risk_type, items in expert_tasks.items()
        }
        
        return {
            "work_list": work_list_dicts,
            "expert_tasks": expert_tasks_dicts
        }
    except Exception as e:
        logger.error(f"Error in manager node: {e}")
        return {"work_list": [], "expert_tasks": {}}


def _format_file_analyses(file_analyses: List[Any]) -> str:
    """格式化文件分析结果用于提示词。"""
    summaries = []
    for analysis in file_analyses:
        summaries.append(
            f"File: {analysis.file_path}\n"
            f"Intent: {analysis.intent_summary}\n"
            f"Potential Risks: {len(analysis.potential_risks)}\n"
        )
    return "\n".join(summaries)

def _format_work_list(work_list: List[Any]) -> str:
    """格式化任务列表用于提示词。"""
    summaries = []
    for w in work_list:
        summaries.append(
            f"File: {w.file_path}\n"
            f"Line Number: {w.line_number}\n"
            f"Confidence: {w.confidence}\n"
            f"Risk Type: {w.risk_type}\n"
            f"Description: {w.description}\n"
        )
    return "\n".join(summaries)

def _get_expanded_format_instructions(parser: PydanticOutputParser) -> str:
    """生成扩展的格式说明（包含嵌套模型结构）。"""
    import json
    
    # Get the JSON schema from the Pydantic model
    schema = WorkListResponse.model_json_schema()
    
    # Expand the schema to resolve $ref references
    def expand_refs(schema_dict: dict, definitions: dict = None) -> dict:
        """Recursively expand $ref references in the schema."""
        if definitions is None:
            definitions = schema_dict.get("$defs", {})
        
        if isinstance(schema_dict, dict):
            if "$ref" in schema_dict:
                # Resolve the reference
                ref_path = schema_dict["$ref"]
                if ref_path.startswith("#/$defs/"):
                    def_name = ref_path.split("/")[-1]
                    if def_name in definitions:
                        # Recursively expand the referenced definition
                        expanded = expand_refs(definitions[def_name], definitions)
                        return expanded
            else:
                # Recursively process all values
                return {k: expand_refs(v, definitions) for k, v in schema_dict.items()}
        elif isinstance(schema_dict, list):
            return [expand_refs(item, definitions) for item in schema_dict]
        else:
            return schema_dict
    
    # Expand the schema
    expanded_schema = expand_refs(schema)
    
    # Remove $defs since we've expanded all references
    if "$defs" in expanded_schema:
        del expanded_schema["$defs"]
    
    # Generate a clean JSON schema string
    schema_str = json.dumps(expanded_schema, indent=2, ensure_ascii=False)
    
    # Get enum values dynamically
    risk_type_values = [rt.value for rt in RiskType]
    risk_type_str = ", ".join([f'"{v}"' for v in risk_type_values])
    
    # Create expanded format instructions
    expanded_instructions = f"""You must respond with a JSON object that matches the following schema:

        {schema_str}

        Important notes:
        - The "risk_type" field must be one of: {risk_type_str}
        - The "line_number" field must be a positive integer (1-indexed)
        - The "confidence" field must be a float between 0.0 and 1.0
        - The "severity" field must be one of: "error", "warning", "info"
        - The "suggestion" field is optional (can be null or omitted)

        Return only the JSON object, without any markdown code blocks or additional text."""
    
    return expanded_instructions


def _convert_lint_errors_to_risk_items(lint_errors: List[Dict[str, Any]]) -> List[RiskItem]:
    """将 lint 错误转换为 RiskItem 对象（risk_type=syntax）。"""
    risk_items = []
    for error in lint_errors:
        try:
            file_path = error.get("file", "")
            line_number = error.get("line", 1)
            message = error.get("message", "")
            severity = error.get("severity", "error")
            code = error.get("code", "")
            
            # Build description with error code if available
            if code:
                description = f"[{code}] {message}"
            else:
                description = message
            
            # Convert single line number to range format [line, line]
            line_num = int(line_number) if line_number else 1
            risk_item = RiskItem(
                risk_type=RiskType.SYNTAX,
                file_path=file_path,
                line_number=[line_num, line_num],  # Must be [start, end] format
                description=description,
                confidence=0.8,  # Lint errors have high confidence from static analysis
                severity=severity,
                suggestion=None  # Expert will provide suggestions
            )
            risk_items.append(risk_item)
        except Exception as e:
            logger.warning(f"Failed to convert lint error to RiskItem: {e}, error: {error}")
            continue
    
    return risk_items


def _group_tasks_by_risk_type(work_list: List[RiskItem]) -> Dict[str, List[RiskItem]]:
    """按风险类型分组任务列表。"""
    grouped = {}
    for item in work_list:
        risk_type_str = item.risk_type.value
        if risk_type_str not in grouped:
            grouped[risk_type_str] = []
        grouped[risk_type_str].append(item)
    
    return grouped
