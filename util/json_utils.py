"""JSON 提取工具函数。

从混合文本（包含 markdown 代码块、解释性文本等）中提取 JSON 对象。
"""

import json
import re
from typing import Optional


def extract_json_from_text(text: str) -> Optional[str]:
    """从文本中提取 JSON 字符串。
    
    支持以下格式：
    1. Markdown 代码块：```json {...} ``` 或 ``` {...} ```
    2. 纯 JSON 对象：{...}
    3. 文本中的 JSON 对象（查找平衡的大括号）
    
    Args:
        text: 包含 JSON 的文本。
    
    Returns:
        提取的 JSON 字符串，如果无法提取则返回 None。
    """
    if not text:
        return None
    
    # 方法1: 提取 markdown 代码块中的 JSON
    # 匹配模式：```json ... ``` 或 ``` ... ```
    json_block_pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
    matches = re.finditer(json_block_pattern, text, re.DOTALL)
    for match in matches:
        try:
            json_str = match.group(1).strip()
            # 验证 JSON 有效性
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError:
            continue
    
    # 方法2: 从文本中提取 JSON 对象（查找平衡的大括号）
    # 查找第一个 { 开始，找到匹配的 }
    brace_count = 0
    start_idx = -1
    
    for i, char in enumerate(text):
        if char == '{':
            if brace_count == 0:
                start_idx = i
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0 and start_idx != -1:
                json_str = text[start_idx:i+1]
                try:
                    # 验证 JSON 有效性
                    json.loads(json_str)
                    return json_str
                except json.JSONDecodeError:
                    # 继续查找下一个可能的 JSON 对象
                    start_idx = -1
                    continue
    
    # 方法3: 尝试直接解析整个文本（去除首尾空白）
    try:
        cleaned_text = text.strip()
        # 如果文本以 { 开头且以 } 结尾，尝试直接解析
        if cleaned_text.startswith('{') and cleaned_text.endswith('}'):
            json.loads(cleaned_text)
            return cleaned_text
    except json.JSONDecodeError:
        pass
    
    return None

