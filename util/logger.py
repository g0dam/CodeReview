"""智能体观察和工具结果的日志工具。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import Config
from util.git_utils import get_repo_name, get_git_info


def _get_log_directory(workspace_root: Path, config: Config, metadata: dict) -> Path:
    """获取当前运行的日志目录路径。"""
    # Get repo name
    repo_name = get_repo_name(workspace_root)
    # Sanitize repo name for filesystem
    repo_name = repo_name.replace("/", "_").replace("\\", "_").replace("..", "")
    
    # Get model name from metadata or config
    model_name = metadata.get("config_provider", config.llm.provider)
    if not model_name:
        model_name = "unknown"
    # Sanitize model name
    model_name = model_name.replace("/", "_").replace("\\", "_")
    
    # Get current branch name from Git
    branch_name, _ = get_git_info(workspace_root)
    if branch_name:
        # Sanitize branch name for filesystem
        branch_name = branch_name.replace("/", "_").replace("\\", "_").replace("..", "").replace(" ", "_")
        # Limit length to avoid filesystem issues
        if len(branch_name) > 50:
            branch_name = branch_name[:50]
    else:
        branch_name = "unknown"
    
    # Generate timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create log directory structure: log/repo_name/model_name/branch_name_timestamp
    log_dir = Path("log") / repo_name / model_name / f"{branch_name}_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    return log_dir


def save_observations_to_log(
    results: dict,
    workspace_root: Path,
    config: Config
) -> Optional[Path]:
    """将智能体观察保存到日志文件。
    
    只保存 expert_analyses.log，不再保存 observations.log。
    日志文件结构：log/repo_name/model_name/timestamp/expert_analyses.log
    """
    metadata = results.get("metadata", {})
    expert_analyses = metadata.get("expert_analyses", [])
    
    # If no expert analyses, return None
    if not expert_analyses:
        return None
    
    # Get log directory
    log_dir = _get_log_directory(workspace_root, config, metadata)
    repo_name = get_repo_name(workspace_root).replace("/", "_").replace("\\", "_").replace("..", "")
    model_name = metadata.get("config_provider", config.llm.provider) or "unknown"
    model_name = model_name.replace("/", "_").replace("\\", "_")
    
    # Save expert analyses to separate file
    if expert_analyses:
        expert_log_file = log_dir / "expert_analyses.log"
        with open(expert_log_file, "w", encoding="utf-8") as f:
            f.write(f"Expert Analysis Log\n")
            f.write(f"{'=' * 80}\n")
            f.write(f"Repository: {repo_name}\n")
            f.write(f"Model: {model_name}\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"{'=' * 80}\n\n")
            
            # Print worklist summary
            work_list = results.get("work_list", [])
            expert_tasks = results.get("expert_tasks", {})
            total_risks = len(work_list)
            
            # Count risks by type
            risk_counts = {}
            for risk in work_list:
                risk_type = risk.get("risk_type", "unknown")
                risk_counts[risk_type] = risk_counts.get(risk_type, 0) + 1
            
            f.write(f"Worklist Summary\n")
            f.write(f"{'=' * 80}\n")
            f.write(f"Total Risks: {total_risks}\n")
            f.write(f"Risk Distribution:\n")
            for risk_type, count in sorted(risk_counts.items()):
                f.write(f"  - {risk_type}: {count}\n")
            f.write(f"{'=' * 80}\n\n")
            
            # Print each expert analysis
            for i, analysis in enumerate(expert_analyses, 1):
                f.write(f"Expert Analysis {i}:\n")
                f.write(f"{'=' * 80}\n")
                f.write(f"Risk Type: {analysis.get('risk_type', 'unknown')}\n")
                f.write(f"File: {analysis.get('file_path', 'unknown')}\n")
                line_number = analysis.get('line_number', [0, 0])
                if isinstance(line_number, list) and len(line_number) == 2:
                    if line_number[0] == line_number[1]:
                        line_str = str(line_number[0])
                    else:
                        line_str = f"{line_number[0]}:{line_number[1]}"
                else:
                    line_str = str(line_number)
                f.write(f"Line: {line_str}\n")
                
                # Add description
                risk_item = analysis.get("risk_item", {})
                description = risk_item.get("description", "")
                if description:
                    f.write(f"Description: {description}\n")
                
                f.write(f"{'-' * 80}\n\n")
                
                # 1. Print analysis result first
                result = analysis.get("result", {})
                if result:
                    f.write(f"Analysis Result:\n")
                    f.write(f"{json.dumps(result, indent=2, ensure_ascii=False)}\n\n")
                
                risk_item = analysis.get("risk_item")
                if risk_item:
                    f.write(f"Risk Item:\n")
                    f.write(f"{json.dumps(risk_item, indent=2, ensure_ascii=False)}\n\n")
                
                # 2. Print conversation history
                messages = analysis.get("messages", [])
                if messages:
                    f.write(f"Conversation History ({len(messages)} messages):\n")
                    f.write(f"{'=' * 80}\n\n")
                    
                    for msg_idx, msg in enumerate(messages, 1):
                        # Get message type
                        msg_type = type(msg).__name__
                        
                        if msg_type == "SystemMessage":
                            f.write(f"Message {msg_idx} [System]:\n")
                            f.write(f"{'-' * 80}\n")
                            content = getattr(msg, 'content', str(msg))
                            f.write(f"{content}\n\n")
                        
                        elif msg_type == "HumanMessage":
                            f.write(f"Message {msg_idx} [Human]:\n")
                            f.write(f"{'-' * 80}\n")
                            content = getattr(msg, 'content', str(msg))
                            f.write(f"{content}\n\n")
                        
                        elif msg_type == "AIMessage":
                            f.write(f"Message {msg_idx} [Assistant]:\n")
                            f.write(f"{'-' * 80}\n")
                            content = getattr(msg, 'content', str(msg))
                            if content:
                                f.write(f"Content:\n{content}\n")
                            # 不输出 tool_calls，因为工具调用信息已经在 ToolMessage 的 content 中
                            f.write(f"\n")
                        
                        elif msg_type == "ToolMessage":
                            f.write(f"Message {msg_idx} [Tool]:\n")
                            f.write(f"{'-' * 80}\n")
                            tool_name = getattr(msg, 'name', 'unknown')
                            content = getattr(msg, 'content', str(msg))
                            tool_call_id = getattr(msg, 'tool_call_id', 'unknown')
                            
                            f.write(f"Tool: {tool_name}\n")
                            f.write(f"Tool Call ID: {tool_call_id}\n")
                            f.write(f"Result:\n")
                            
                            # Try to parse content as JSON, if it's already JSON, keep it formatted
                            try:
                                # If content is already a dict/JSON, format it
                                if isinstance(content, (dict, list)):
                                    f.write(f"{json.dumps(content, indent=4, ensure_ascii=False)}\n")
                                else:
                                    # Try to parse as JSON string
                                    parsed = json.loads(content)
                                    f.write(f"{json.dumps(parsed, indent=4, ensure_ascii=False)}\n")
                            except (json.JSONDecodeError, TypeError):
                                # If not JSON, write as-is
                                f.write(f"{content}\n")
                            f.write(f"\n")
                        
                        else:
                            # Unknown message type
                            f.write(f"Message {msg_idx} [{msg_type}]:\n")
                            f.write(f"{'-' * 80}\n")
                            content = getattr(msg, 'content', str(msg))
                            f.write(f"{content}\n\n")
                
                f.write(f"\n")
        
        # Return expert log file
        return expert_log_file
    
    return None
