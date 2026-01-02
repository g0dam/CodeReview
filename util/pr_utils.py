"""PR（拉取请求）处理工具，用于 diff 加载和结果格式化。"""

import json
from pathlib import Path
from typing import Optional

from core.config import Config
from util.git_utils import get_repo_name
from util.logger import save_observations_to_log


def load_diff_from_file(file_path: Path) -> str:
    """从文件加载 Git diff。
    
    Raises:
        FileNotFoundError: 文件不存在。
        IOError: 文件无法读取。
    """
    file_path = Path(file_path).resolve()
    
    if not file_path.exists():
        raise FileNotFoundError(f"Diff file not found: {file_path}")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        raise IOError(f"Error reading diff file: {e}")


def print_review_results(results: dict, workspace_root: Optional[Path] = None, config: Optional[Config] = None) -> None:
    """以格式化方式打印审查结果。"""
    print("\n" + "=" * 80)
    print("CODE REVIEW RESULTS")
    print("=" * 80)
    
    # Changed files (for multi-agent workflow) or focus files (for old workflow)
    changed_files = results.get("changed_files", [])
    focus_files = results.get("focus_files", [])
    files_to_show = changed_files if changed_files else focus_files
    
    print(f"\n📋 Changed Files ({len(files_to_show)}):")
    if files_to_show:
        for i, file_path in enumerate(files_to_show, 1):
            print(f"  {i}. {file_path}")
    else:
        print("  (none)")
    
    # Issues - support both old format (identified_issues) and new format (confirmed_issues)
    identified_issues = results.get("identified_issues", [])
    confirmed_issues = results.get("confirmed_issues", [])
    issues = confirmed_issues if confirmed_issues else identified_issues
    
    print(f"\n🔍 Issues Found ({len(issues)}):")
    
    if not issues:
        print("  ✅ No issues found!")
    else:
        # Group by severity
        by_severity = {"error": [], "warning": [], "info": []}
        for issue in issues:
            # Support both old format (severity) and new format (RiskItem with severity)
            severity = issue.get("severity", "info")
            by_severity.get(severity, by_severity["info"]).append(issue)
        
        for severity in ["error", "warning", "info"]:
            severity_issues = by_severity[severity]
            if severity_issues:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}[severity]
                print(f"\n  {icon} {severity.upper()} ({len(severity_issues)}):")
                for issue in severity_issues:
                    # Support both old format and new RiskItem format
                    file_path = issue.get("file_path") or issue.get("file", "unknown")
                    line_number = issue.get("line_number") or issue.get("line", 0)
                    # Format line number range: (10, 15) -> "10:15", (10, 10) or 10 -> "10"
                    if isinstance(line_number, (list, tuple)) and len(line_number) == 2:
                        start, end = line_number
                        line = f"{start}:{end}" if start != end else str(start)
                    else:
                        line = str(line_number) if line_number else "0"
                    message = issue.get("description") or issue.get("message", "")
                    suggestion = issue.get("suggestion", "")
                    risk_type = issue.get("risk_type", "")
                    confidence = issue.get("confidence")
                    
                    # Format risk type if available
                    risk_type_str = f" [{risk_type}]" if risk_type else ""
                    confidence_str = f" (confidence: {confidence:.2f})" if confidence is not None else ""
                    
                    print(f"    • {file_path}:{line}{risk_type_str}{confidence_str}")
                    print(f"      {message}")
                    if suggestion:
                        print(f"      💡 Suggestion: {suggestion}")
    
    # Final report (for multi-agent workflow)
    final_report = results.get("final_report", "")
    if final_report:
        print(f"\n📄 Final Report:")
        print("  " + "=" * 76)
        # Print first 500 characters of the report
        report_preview = final_report[:500] + "..." if len(final_report) > 500 else final_report
        for line in report_preview.split("\n"):
            print(f"  {line}")
        if len(final_report) > 500:
            print(f"  ... (truncated, {len(final_report)} total characters)")
        print("  " + "=" * 76)
    
    # Metadata (skip langchain_tools and other verbose fields)
    metadata = results.get("metadata", {})
    if metadata:
        print(f"\n📊 Metadata:")
        for key, value in metadata.items():
            # Skip printing observations in metadata (will be in log file)
            if key == "agent_observations":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} observations] (saved to log)")
            elif key == "agent_tool_results":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} tool calls] (saved to log)")
            elif key == "expert_analyses":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} expert analyses] (saved to log)")
            elif key in ["llm_provider", "config", "tools", "langchain_tools"]:
                # Skip non-serializable objects and langchain_tools
                continue
            else:
                print(f"  • {key}: {value}")
    
    # Save observations and expert analyses to log files
    if workspace_root and config:
        try:
            log_file = save_observations_to_log(results, workspace_root, config)
            if log_file:
                print(f"\n📝 Logs saved:")
                print(f"   • Expert Analyses: {log_file}")
        except Exception as e:
            print(f"\n⚠️  Warning: Could not save logs: {e}")
    
    print("\n" + "=" * 80)


def make_results_serializable(obj: dict) -> dict:
    """移除字典中的不可序列化对象（如 ChatModel、Config、tools）。
    
    同时优化结果结构：
    - 移除 diff_context 字段
    - 移除 confirmed_issues 字段
    - 移除 metadata 字段
    - 合并 work_list, expert_tasks, expert_results 为 risk_analyses 字段
    - final_report 字段放在最后
    - risk_analyses 中不包含 validated_item
    
    Args:
        obj: 可能包含不可序列化对象的字典。
    
    Returns:
        仅包含可序列化值的字典。
    """
    if not isinstance(obj, dict):
        return obj
    
    result = {}
    for key, value in obj.items():
        # Remove diff_context field
        if key == "diff_context":
            continue
        
        if key == "metadata":
            # Skip metadata - we'll access expert_analyses from it but not include it in output
            continue
        elif key in ["work_list", "expert_tasks", "expert_results", "confirmed_issues"]:
            # Skip these keys - they will be merged into risk_analyses or removed
            continue
        elif key == "final_report":
            # Skip final_report here - will be added at the end
            continue
        elif isinstance(value, dict):
            result[key] = make_results_serializable(value)
        elif isinstance(value, list):
            result[key] = [
                make_results_serializable(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            # Try to serialize, skip if not serializable
            try:
                json.dumps(value)
                result[key] = value
            except (TypeError, ValueError):
                result[key] = str(value)
    
    # Merge work_list, expert_tasks, expert_results into risk_analyses
    expert_analyses = obj.get("metadata", {}).get("expert_analyses", [])
    if expert_analyses:
        # Create a map from (file_path, line_number, risk_type) to expert_analysis
        analysis_map = {}
        for analysis in expert_analyses:
            file_path = analysis.get("file_path", "")
            line_number = analysis.get("line_number", [0, 0])
            risk_type = analysis.get("risk_type", "")
            key = (file_path, tuple(line_number) if isinstance(line_number, list) else line_number, risk_type)
            analysis_map[key] = analysis
        
        # Build risk_analyses list by matching work_list items with expert_analyses
        risk_analyses = []
        work_list = obj.get("work_list", [])
        
        for risk_item in work_list:
            file_path = risk_item.get("file_path", "")
            line_number = risk_item.get("line_number", [0, 0])
            risk_type = risk_item.get("risk_type", "")
            key = (file_path, tuple(line_number) if isinstance(line_number, list) else line_number, risk_type)
            
            analysis = analysis_map.get(key, {})
            
            # Build merged entry (without validated_item)
            merged_entry = {
                "risk_item": risk_item,  # 原始风险项
                "result": analysis.get("result", {}),  # 分析结果
                "messages": serialize_messages(analysis.get("messages", []))  # 对话历史
            }
            risk_analyses.append(merged_entry)
        
        result["risk_analyses"] = risk_analyses
    
    # Add final_report at the end
    final_report = obj.get("final_report", "")
    if final_report:
        result["final_report"] = final_report
    
    return result


def serialize_messages(messages: list) -> list:
    """序列化 LangChain 消息列表。
    
    不包含 tool_calls 字段，因为工具调用信息已经在 ToolMessage 的 content 中。
    
    Args:
        messages: LangChain 消息列表。
    
    Returns:
        可序列化的消息字典列表。
    """
    serialized = []
    for msg in messages:
        msg_dict = {
            "type": type(msg).__name__,
            "content": getattr(msg, 'content', str(msg))
        }
        
        # 不包含 tool_calls 字段，因为工具调用信息已经在 ToolMessage 的 content 中
        
        if hasattr(msg, 'name'):
            msg_dict["name"] = msg.name
        
        if hasattr(msg, 'tool_call_id'):
            msg_dict["tool_call_id"] = msg.tool_call_id
        
        serialized.append(msg_dict)
    
    return serialized
