"""代码审查工作流的专家执行节点。

处理专家智能体的并行执行（并发控制）。
每个专家组并发处理特定风险类型的任务。
"""

import asyncio
import logging
import json
import re
from typing import Dict, Any, List, Optional
from core.state import ReviewState, RiskItem, RiskType
from core.llm import LLMProvider
from core.config import Config
from tools.repo_tools import FetchRepoMapTool
from tools.file_tools import ReadFileTool
from tools.grep_tool import GrepTool
from agents.prompts import render_prompt_template
from util.file_utils import read_file_content
from util.diff_utils import extract_file_diff

logger = logging.getLogger(__name__)


def format_line_number(line_number: tuple[int, int]) -> str:
    """格式化行号范围为字符串（"10:15" 或 "10"）。"""
    start_line, end_line = line_number
    if start_line == end_line:
        return str(start_line)
    else:
        return f"{start_line}:{end_line}"


async def expert_execution_node(state: ReviewState) -> Dict[str, Any]:
    """并行执行专家智能体（并发控制）。
    
    Returns:
        包含 'expert_results' 键的字典。
    """
    print("\n" + "="*80)
    print("🔬 [节点3] Expert Execution - 并行执行专家组")
    print("="*80)
    
    # Get dependencies from metadata
    llm_provider: LLMProvider = state.get("metadata", {}).get("llm_provider")
    config: Config = state.get("metadata", {}).get("config")
    tools = state.get("metadata", {}).get("tools", [])
    
    if not llm_provider:
        logger.error("LLM provider not found in metadata")
        return {"expert_results": {}}
    
    if not config:
        logger.error("Config not found in metadata")
        return {"expert_results": {}}
    
    expert_tasks_dicts = state.get("expert_tasks", {})
    diff_context = state.get("diff_context", "")
    
    if not expert_tasks_dicts:
        print("  ⚠️  没有专家任务需要执行")
        logger.warning("No expert tasks to execute")
        return {"expert_results": {}}
    
    # Convert dicts to Pydantic models for processing
    from core.state import RiskItem
    expert_tasks = {
        risk_type: [RiskItem(**item) if isinstance(item, dict) else item for item in items]
        for risk_type, items in expert_tasks_dicts.items()
    }
    
    # Get concurrency limit from config
    max_concurrent = config.system.max_concurrent_llm_requests
    
    print(f"  📥 接收专家组任务: {len(expert_tasks)} 个专家组")
    total_tasks = sum(len(tasks) for tasks in expert_tasks.values())
    print(f"  📊 总任务数: {total_tasks}")
    print(f"  🔒 并发控制: Semaphore(max={max_concurrent})")
    print(f"  📋 专家组详情:")
    for risk_type, tasks in expert_tasks.items():
        print(f"     • {risk_type}: {len(tasks)} 个任务")
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_concurrent)
    
    # Create tool instances if not provided
    if not tools:
        workspace_root = config.system.workspace_root
        asset_key = config.system.asset_key
        tools = [
            # FetchRepoMapTool(asset_key=asset_key),
            ReadFileTool(workspace_root=workspace_root),
            GrepTool(workspace_root=workspace_root)
        ]
    
    # Execute all expert groups in parallel
    expert_results = {}
    
    # Create tasks for each risk type
    tasks = []
    for risk_type_str, risk_items in expert_tasks.items():
        task = run_expert_group(
            risk_type_str=risk_type_str,
            tasks=risk_items,
            global_state=state,
            llm_provider=llm_provider,
            tools=tools,
            semaphore=semaphore,
            diff_context=diff_context
        )
        tasks.append((risk_type_str, task))
    
    # Wait for all expert groups to complete
    print(f"\n  🚀 开始并行执行 {len(expert_tasks)} 个专家组...")
    results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)
    
    # Collect results
    for (risk_type_str, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            print(f"  ❌ 专家组 {risk_type_str} 执行失败: {result}")
            logger.error(f"Error in expert group {risk_type_str}: {result}")
            expert_results[risk_type_str] = []
        else:
            print(f"  ✅ 专家组 {risk_type_str} 完成: {len(result)} 个结果")
            expert_results[risk_type_str] = result
    
    total_results = sum(len(r) for r in expert_results.values())
    print(f"\n  ✅ Expert Execution 完成!")
    print(f"     - 完成专家组: {len(expert_results)}")
    print(f"     - 总结果数: {total_results}")
    print("="*80)
    logger.info(f"Expert execution completed: {len(expert_results)} groups, "
                f"{total_results} total results")
    
    # Convert Pydantic models to dicts for state (LangGraph TypedDict compatibility)
    expert_results_dicts = {
        risk_type: [item.model_dump() for item in items]
        for risk_type, items in expert_results.items()
    }
    
    return {"expert_results": expert_results_dicts}


async def run_expert_group(
    risk_type_str: str,
    tasks: List[RiskItem],
    global_state: ReviewState,
    llm_provider: LLMProvider,
    tools: List[Any],
    semaphore: asyncio.Semaphore,
    diff_context: str
) -> List[RiskItem]:
    """Run expert group for a specific risk type.
    
    This function processes all tasks for a given risk type, with concurrency
    control to limit simultaneous LLM API calls.
    
    Args:
        risk_type_str: Risk type as string (e.g., "null_safety", "concurrency", "security").
        tasks: List of RiskItem objects to process.
        global_state: Global workflow state for context.
        llm_provider: LLM provider instance.
        tools: List of available tools (ReadFileTool, FetchRepoMapTool).
        semaphore: Semaphore for concurrency control.
        diff_context: Full diff context.
    
    Returns:
        List of validated RiskItem objects.
    """
    if not tasks:
        return []
    
    print(f"    🔍 [{risk_type_str}] 开始处理 {len(tasks)} 个任务...")
    
    # Create tool dictionary for easy access
    tool_dict = {tool.name: tool for tool in tools}
    
    # Process each task with concurrency control
    async def process_task(task: RiskItem) -> Optional[RiskItem]:
        """Process a single task with concurrency control."""
        async with semaphore:
            try:
                return await _process_risk_item(
                    risk_item=task,
                    risk_type_str=risk_type_str,
                    global_state=global_state,
                    llm_provider=llm_provider,
                    tool_dict=tool_dict,
                    diff_context=diff_context
                )
            except Exception as e:
                line_str = format_line_number(task.line_number)
                logger.error(f"Error processing risk item {task.file_path}:{line_str}: {e}")
                return None
    
    # Process all tasks concurrently (with semaphore limiting)
    results = await asyncio.gather(*[process_task(task) for task in tasks])
    
    # Filter out None results (errors)
    validated_results = [r for r in results if r is not None]
    
    print(f"    ✅ [{risk_type_str}] 完成: {len(validated_results)}/{len(tasks)} 个任务验证成功")
    logger.info(f"Expert group {risk_type_str}: processed {len(tasks)} tasks, "
                f"{len(validated_results)} validated results")
    
    return validated_results


async def _process_risk_item(
    risk_item: RiskItem,
    risk_type_str: str,
    global_state: ReviewState,
    llm_provider: LLMProvider,
    tool_dict: Dict[str, Any],
    diff_context: str
) -> Optional[RiskItem]:
    """Process a single risk item using expert LLM with multi-turn conversation.
    
    This function implements a ReAct-like loop where the expert agent can:
    1. Analyze the risk item
    2. Call tools to gather more context
    3. Continue analysis based on tool results
    4. Repeat until sufficient information is gathered
    5. Provide final validated result
    
    Args:
        risk_item: The risk item to validate/analyze.
        risk_type_str: Risk type string.
        global_state: Global workflow state.
        llm_provider: LLM provider instance.
        tool_dict: Dictionary of available tools.
        diff_context: Full diff context.
    
    Returns:
        Validated RiskItem or None if validation fails.
    """
    # Initialize expert analysis log entry
    expert_analysis = {
        "risk_type": risk_type_str,
        "file_path": risk_item.file_path,
        "line_number": risk_item.line_number,
        "conversation_turns": [],
        "final_response": "",
        "validated_item": None
    }
    
    max_iterations = 10  # Maximum number of conversation turns
    conversation_history = []  # Store conversation turns
    
    try:
        # Load initial expert prompt
        # Note: validation_logic_examples 由用户手动在模板文件中填写，不需要动态填充
        # Format line number range for display in prompts
        line_number_str = format_line_number(risk_item.line_number)
        
        # Get config from global_state to read file content
        config = global_state.get("metadata", {}).get("config")
        file_content = read_file_content(risk_item.file_path, config) if config else ""
        
        # Format available tools with descriptions
        tool_descriptions = []
        for tool_name, tool in tool_dict.items():
            description = getattr(tool, 'description', f'Tool: {tool_name}')
            tool_descriptions.append(f"- **{tool_name}**: {description}")
        available_tools_text = "\n".join(tool_descriptions)
        
        try: 
            initial_prompt = render_prompt_template(
                f"expert_{risk_type_str}",
                risk_type=risk_type_str,
                # risk_item=risk_item.model_dump(),
                file_path=risk_item.file_path,
                line_number=line_number_str,  # Format as string for prompt display
                description=risk_item.description,
                diff_context=extract_file_diff(diff_context, risk_item.file_path),
                file_content=file_content,
                available_tools=available_tools_text,
                validation_logic_examples=""  # 占位符，用户会在模板文件中手动填写内容
            )
        except FileNotFoundError:
            # Fallback to generic expert prompt
            initial_prompt = render_prompt_template(
                "expert_generic",
                risk_type=risk_type_str,
                # risk_item=risk_item.model_dump(),
                file_path=risk_item.file_path,
                line_number=line_number_str,  # Format as string for prompt display
                description=risk_item.description,
                diff_context=extract_file_diff(diff_context, risk_item.file_path),
                file_content=file_content,
                available_tools=available_tools_text
            )
        
        # Start conversation loop
        current_prompt = initial_prompt
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            turn = {
                "iteration": iteration,
                "prompt": current_prompt,
                "response": "",
                "tool_calls": [],
                "tool_results": {}
            }
            
            # Get LLM response
            response = await llm_provider.generate(current_prompt, temperature=0.3)
            turn["response"] = response
            
            # Check if this is a final answer
            tool_calls = _extract_tool_calls(response)
            
            # Check for explicit final answer indicators
            has_final_answer_marker = (
                "Final Answer:" in response or 
                "final answer:" in response.lower() or
                "Final Result:" in response or
                "final result:" in response.lower()
            )
            
            # Check if response contains JSON with validated risk item
            has_json_result = (
                "{" in response and 
                ("risk_type" in response or "file_path" in response or "description" in response)
            )
            
            # Stop conditions:
            # 1. No tool calls AND (explicit final answer OR JSON result OR after first iteration)
            # 2. Reached max iterations
            should_stop = (
                not tool_calls and (
                    has_final_answer_marker or 
                    has_json_result or 
                    iteration > 1
                )
            )
            
            if should_stop:
                expert_analysis["final_response"] = response
                break
            
            # Execute tool calls if any
            if tool_calls:
                tool_results_summary = {}
                for tool_call in tool_calls:
                    tool_name = tool_call.get("tool")
                    tool_input = tool_call.get("input", {})
                    
                    turn["tool_calls"].append({
                        "tool": tool_name,
                        "input": tool_input
                    })
                    
                    if tool_name in tool_dict:
                        try:
                            tool_result = await tool_dict[tool_name].run(**tool_input)
                            turn["tool_results"][tool_name] = tool_result
                            tool_results_summary[tool_name] = tool_result
                        except Exception as e:
                            logger.warning(f"Tool call failed: {tool_name}: {e}")
                            error_result = {"error": str(e)}
                            turn["tool_results"][tool_name] = error_result
                            tool_results_summary[tool_name] = error_result
                
                # Build next prompt with conversation history and tool results
                conversation_context = "\n\n".join([
                    f"Turn {t['iteration']}:\nResponse: {t['response']}"
                    for t in conversation_history
                ])
                
                # Build tool results summary with better formatting
                tool_results_text = "\n".join([
                    f"{tool_name}:\n{json.dumps(result, indent=2, ensure_ascii=False)}"
                    for tool_name, result in tool_results_summary.items()
                ])
                
                if conversation_context:
                    current_prompt = (
                        initial_prompt + 
                        "\n\n=== Previous Conversation ===" +
                        "\n" + conversation_context +
                        "\n\n=== Current Turn ===" +
                        "\n" + response +
                        "\n\n=== Tool Results ===" +
                        "\n" + tool_results_text +
                        "\n\n=== Instructions ===" +
                        "\nBased on the tool results above, please:" +
                        "\n1. Continue your analysis if you need more information (call more tools)" +
                        "\n2. Provide your final validated result in JSON format if you have sufficient information" +
                        "\n3. Format your final result as: {\"risk_type\": \"...\", \"file_path\": \"...\", \"line_number\": [start, end], \"description\": \"...\", \"confidence\": ..., \"severity\": \"...\", \"suggestion\": \"...\"}"
                        "\n   NOTE: line_number MUST be an array [start, end]. For single line, use [line, line]."
                    )
                else:
                    current_prompt = (
                        initial_prompt +
                        "\n\n=== Your Response ===" +
                        "\n" + response +
                        "\n\n=== Tool Results ===" +
                        "\n" + tool_results_text +
                        "\n\n=== Instructions ===" +
                        "\nBased on the tool results above, please:" +
                        "\n1. Continue your analysis if you need more information (call more tools)" +
                        "\n2. Provide your final validated result in JSON format if you have sufficient information" +
                        "\n3. Format your final result as: {\"risk_type\": \"...\", \"file_path\": \"...\", \"line_number\": [start, end], \"description\": \"...\", \"confidence\": ..., \"severity\": \"...\", \"suggestion\": \"...\"}"
                        "\n   NOTE: line_number MUST be an array [start, end]. For single line, use [line, line]."
                    )
            else:
                # No tool calls - check if this is first turn
                if iteration == 1:
                    # First turn with no tool calls, agent thinks it has enough info
                    expert_analysis["final_response"] = response
                    break
                else:
                    # Subsequent turn with no tool calls, agent should provide final answer
                    # But it didn't, so we'll use this response as final
                    expert_analysis["final_response"] = response
                    break
            
            # Add turn to conversation history
            conversation_history.append(turn)
            expert_analysis["conversation_turns"].append(turn)
        
        # If we reached max iterations, use the last response
        if iteration >= max_iterations:
            expert_analysis["final_response"] = response
            line_str = format_line_number(risk_item.line_number)
            logger.warning(f"Reached max iterations ({max_iterations}) for risk item {risk_item.file_path}:{line_str}")
        
        # Parse final response to get validated risk item
        validated_item = _parse_expert_response(expert_analysis["final_response"], risk_item)
        expert_analysis["validated_item"] = validated_item.model_dump() if validated_item else None
        
        # Store expert analysis in metadata for logging
        if "metadata" not in global_state:
            global_state["metadata"] = {}
        if "expert_analyses" not in global_state["metadata"]:
            global_state["metadata"]["expert_analyses"] = []
        global_state["metadata"]["expert_analyses"].append(expert_analysis)

        print('\n最终结果')
        print(validated_item.risk_type,validated_item.file_path,validated_item.line_number,validated_item.confidence)
        print(validated_item.description)
        print('\n历史过程')
        for i,conv in enumerate(conversation_history):
            print(i, conv['response'])
        print('\n分析过程')
        print(expert_analysis['final_response'])
        return validated_item
    except Exception as e:
        logger.error(f"Error processing risk item: {e}")
        expert_analysis["error"] = str(e)
        # Store error analysis in metadata
        if "metadata" not in global_state:
            global_state["metadata"] = {}
        if "expert_analyses" not in global_state["metadata"]:
            global_state["metadata"]["expert_analyses"] = []
        global_state["metadata"]["expert_analyses"].append(expert_analysis)
        return None


def _extract_tool_calls(response: str) -> List[Dict[str, Any]]:
    """Extract tool calls from LLM response.
    
    Looks for patterns like:
    - Action: tool_name\nAction Input: {"param": "value"}
    - {"tool": "tool_name", "input": {...}} (single line or multi-line)
    - ```json\n{"tool": "tool_name", "input": {...}}\n```
    
    Args:
        response: LLM response text.
    
    Returns:
        List of tool call dictionaries.
    """
    tool_calls = []
    
    # First, try to extract JSON from code blocks
    # Pattern: ```json\n{...}\n```
    code_block_pattern = r'```(?:json)?\s*\n(.*?)\n```'
    code_block_matches = re.finditer(code_block_pattern, response, re.DOTALL)
    for match in code_block_matches:
        json_content = match.group(1).strip()
        try:
            # Try to parse as complete JSON object
            parsed = json.loads(json_content)
            if isinstance(parsed, dict) and "tool" in parsed and "input" in parsed:
                tool_calls.append({
                    "tool": parsed["tool"],
                    "input": parsed["input"]
                })
        except json.JSONDecodeError:
            # If not a complete JSON, try to extract tool call from it
            pass
    
    # Pattern 1: Action: tool_name\nAction Input: {...}
    pattern1 = r'Action:\s*(\w+)\s*\n\s*Action Input:\s*({.*?})'
    for match in re.finditer(pattern1, response, re.DOTALL):
        tool_name = match.group(1)
        try:
            action_input = json.loads(match.group(2))
            tool_calls.append({"tool": tool_name, "input": action_input})
        except json.JSONDecodeError:
            pass
    
    # Pattern 2: {"tool": "tool_name", "input": {...}} (single line)
    pattern2 = r'\{"tool":\s*"(\w+)",\s*"input":\s*({.*?})\}'
    for match in re.finditer(pattern2, response, re.DOTALL):
        tool_name = match.group(1)
        try:
            action_input = json.loads(match.group(2))
            tool_calls.append({"tool": tool_name, "input": action_input})
        except json.JSONDecodeError:
            pass
    
    # Pattern 3: Multi-line JSON with tool and input
    # Match JSON objects that span multiple lines
    pattern3 = r'\{\s*"tool"\s*:\s*"(\w+)"\s*,\s*"input"\s*:\s*(\{.*?\})\s*\}'
    for match in re.finditer(pattern3, response, re.DOTALL):
        tool_name = match.group(1)
        try:
            # Try to parse the input as JSON
            input_str = match.group(2)
            action_input = json.loads(input_str)
            tool_calls.append({"tool": tool_name, "input": action_input})
        except json.JSONDecodeError:
            # If input parsing fails, try to extract the whole JSON object
            try:
                full_json = match.group(0)
                parsed = json.loads(full_json)
                if "tool" in parsed and "input" in parsed:
                    tool_calls.append({
                        "tool": parsed["tool"],
                        "input": parsed["input"]
                    })
            except json.JSONDecodeError:
                pass
    
    # Pattern 4: Try to find any JSON object with "tool" and "input" keys (most flexible)
    # This handles cases where JSON might be formatted in various ways
    json_object_pattern = r'\{[^{}]*"tool"[^{}]*"input"[^{}]*\}'
    # More comprehensive pattern for nested JSON
    nested_json_pattern = r'\{(?:[^{}]|\{[^{}]*\})*"tool"(?:[^{}]|\{[^{}]*\})*"input"(?:[^{}]|\{[^{}]*\})*(?:\{[^{}]*\})*\}'
    
    for pattern in [nested_json_pattern, json_object_pattern]:
        for match in re.finditer(pattern, response, re.DOTALL):
            json_str = match.group(0)
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict) and "tool" in parsed and "input" in parsed:
                    # Check if we already have this tool call
                    existing = any(
                        tc.get("tool") == parsed["tool"] and 
                        tc.get("input") == parsed["input"]
                        for tc in tool_calls
                    )
                    if not existing:
                        tool_calls.append({
                            "tool": parsed["tool"],
                            "input": parsed["input"]
                        })
            except json.JSONDecodeError:
                pass
    
    return tool_calls


def _parse_expert_response(response: str, original_item: RiskItem) -> RiskItem:
    """Parse expert LLM response to get validated risk item.
    
    Args:
        response: LLM response string.
        original_item: Original risk item.
    
    Returns:
        Validated/updated RiskItem.
    """
    try:
        # Try to parse as JSON
        response_clean = response.strip()
        if response_clean.startswith("```json"):
            response_clean = response_clean[7:]
        if response_clean.startswith("```"):
            response_clean = response_clean[3:]
        if response_clean.endswith("```"):
            response_clean = response_clean[:-3]
        response_clean = response_clean.strip()
        
        try:
            data = json.loads(response_clean)
            
            # Update original item with validated data
            validated_item = RiskItem(
                risk_type=RiskType(data.get("risk_type", original_item.risk_type.value)),
                file_path=data.get("file_path", original_item.file_path),
                line_number=data.get("line_number", original_item.line_number),
                description=data.get("description", original_item.description),
                confidence=float(data.get("confidence", original_item.confidence)),
                severity=data.get("severity", original_item.severity),
                suggestion=data.get("suggestion", original_item.suggestion)
            )
            return validated_item
        except json.JSONDecodeError:
            # If JSON parsing fails, check if response confirms the risk
            # Simple heuristic: if response contains "confirmed" or "valid", keep it
            response_lower = response.lower()
            if "confirmed" in response_lower or "valid" in response_lower:
                # Update confidence based on confirmation
                return RiskItem(
                    risk_type=original_item.risk_type,
                    file_path=original_item.file_path,
                    line_number=original_item.line_number,
                    description=original_item.description,
                    confidence=min(1.0, original_item.confidence + 0.2),
                    severity=original_item.severity,
                    suggestion=original_item.suggestion or response[:200]
                )
            else:
                # Risk not confirmed, reduce confidence
                return RiskItem(
                    risk_type=original_item.risk_type,
                    file_path=original_item.file_path,
                    line_number=original_item.line_number,
                    description=original_item.description,
                    confidence=max(0.0, original_item.confidence - 0.2),
                    severity=original_item.severity,
                    suggestion=original_item.suggestion
                )
    except Exception as e:
        logger.warning(f"Error parsing expert response: {e}, using original item")
        return original_item
