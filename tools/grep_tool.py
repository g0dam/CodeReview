"""代码审查系统的全库搜索工具。

提供在代码库中搜索字符串或正则表达式的功能，返回带有详细上下文的结构化结果。
"""

import functools
import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from tools.base import BaseTool


class GrepInput(BaseModel):
    """Grep 工具的输入模式。
    
    Attributes:
        pattern: 搜索关键词或正则表达式。
        is_regex: 是否启用正则表达式匹配，默认为 False。
        case_sensitive: 是否区分大小写，默认为 True。
        include_patterns: 文件名匹配模式列表（如 ["*.py", "*.ts"]），默认为 ["*"]。
        exclude_patterns: 排除的文件模式列表，默认为空列表。
        context_lines: 返回匹配行前后的上下文行数，默认为 10。
        max_results: 最大返回的匹配块数量，默认为 50。
    """
    
    pattern: str = Field(..., description="搜索关键词或正则表达式")
    is_regex: bool = Field(default=False, description="是否启用正则表达式匹配")
    case_sensitive: bool = Field(default=True, description="是否区分大小写")
    include_patterns: List[str] = Field(default=["*"], description="文件名匹配模式列表")
    exclude_patterns: List[str] = Field(default=[], description="排除的文件模式列表")
    context_lines: int = Field(default=10, description="返回匹配行前后的上下文行数")
    max_results: int = Field(default=50, description="最大返回的匹配块数量")


def _is_binary_file(file_path: Path) -> bool:
    """检查文件是否为二进制文件。
    
    Args:
        file_path: 文件路径。
    
    Returns:
        如果文件可能是二进制文件，返回 True。
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(8192)
            # 检查是否包含空字节或大量非文本字符
            if b"\x00" in chunk:
                return True
            # 检查是否大部分是可打印字符或常见空白字符
            text_chars = sum(1 for byte in chunk if 32 <= byte < 127 or byte in (9, 10, 13))
            if len(chunk) > 0 and text_chars / len(chunk) < 0.7:
                return True
    except Exception:
        return True
    return False


def _should_skip_directory(dir_name: str) -> bool:
    """判断是否应该跳过某个目录。
    
    Args:
        dir_name: 目录名称。
    
    Returns:
        如果应该跳过该目录，返回 True。
    """
    skip_patterns = {
        ".git",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "env",
        ".env",
        "dist",
        "build",
        ".idea",
        ".vscode",
        ".DS_Store",
    }
    return dir_name in skip_patterns or dir_name.startswith(".")


@functools.lru_cache(maxsize=128)
def _grep_internal(
    repo_root: str,
    pattern: str,
    is_regex: bool,
    case_sensitive: bool,
    include_patterns: tuple,
    exclude_patterns: tuple,
    context_lines: int,
    max_results: int,
) -> str:
    """内部 grep 实现，使用 LRU 缓存。
    
    Args:
        repo_root: 仓库根目录路径。
        pattern: 搜索模式。
        is_regex: 是否使用正则表达式。
        case_sensitive: 是否区分大小写。
        include_patterns: 包含的文件模式元组（用于缓存）。
        exclude_patterns: 排除的文件模式元组（用于缓存）。
        context_lines: 上下文行数。
        max_results: 最大结果数。
    
    Returns:
        格式化的搜索结果字符串。
    """
    repo_path = Path(repo_root)
    if not repo_path.exists() or not repo_path.is_dir():
        return f"Error: Repository root does not exist: {repo_root}"
    
    # 编译正则表达式（如果需要）
    if is_regex:
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"Error: Invalid regex pattern: {pattern}\n{str(e)}"
    else:
        # 转义特殊字符用于普通字符串搜索
        escaped_pattern = re.escape(pattern)
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(escaped_pattern, flags)
    
    # 转换元组回列表
    include_list = list(include_patterns)
    exclude_list = list(exclude_patterns)
    
    results = []
    result_count = 0
    
    # 遍历文件
    for root, dirs, files in os.walk(repo_path):
        # 过滤目录
        dirs[:] = [d for d in dirs if not _should_skip_directory(d)]
        
        for file_name in files:
            file_path = Path(root) / file_name
            
            # 检查文件是否匹配包含模式
            matches_include = any(
                fnmatch.fnmatch(file_name, pattern) or fnmatch.fnmatch(str(file_path.relative_to(repo_path)), pattern)
                for pattern in include_list
            )
            
            # 检查文件是否匹配排除模式
            matches_exclude = any(
                fnmatch.fnmatch(file_name, pattern) or fnmatch.fnmatch(str(file_path.relative_to(repo_path)), pattern)
                for pattern in exclude_list
            )
            
            if not matches_include or matches_exclude:
                continue
            
            # 跳过二进制文件
            if _is_binary_file(file_path):
                continue
            
            # 读取文件并搜索
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except Exception:
                continue
            
            # 在每一行中搜索
            for line_num, line in enumerate(lines, start=1):
                if regex.search(line):
                    # 计算上下文范围
                    start_line = max(1, line_num - context_lines)
                    end_line = min(len(lines), line_num + context_lines)
                    
                    # 构建上下文
                    context_lines_list = []
                    for ctx_line_num in range(start_line, end_line + 1):
                        ctx_line = lines[ctx_line_num - 1]  # 转换为 0-based 索引
                        context_lines_list.append(f"{ctx_line_num}: {ctx_line.rstrip()}")
                    
                    # 构建结果块
                    relative_path = file_path.relative_to(repo_path)
                    result_block = f"""File: {relative_path}
Match: Line {line_num}: {line.rstrip()}
Context (Lines {start_line}-{end_line}):
{"\n".join(context_lines_list)}
--------------------------------------------------"""
                    
                    results.append(result_block)
                    result_count += 1
                    
                    if result_count >= max_results:
                        break
            
            if result_count >= max_results:
                break
        
        if result_count >= max_results:
            break
    
    if not results:
        return f"No matches found for pattern: {pattern}"
    
    return "\n\n".join(results)


@tool
async def run_grep(
    pattern: str,
    is_regex: bool = False,
    case_sensitive: bool = True,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    context_lines: int = 10,
    max_results: int = 50,
) -> str:
    """Search for a string or regex in the codebase. Returns the file path, the specific matched line, and the surrounding code context.
    
    This tool searches through the codebase for a given pattern (string or regex) and returns
    structured results with file paths, matched lines, and surrounding context.
    
    Args:
        pattern: The search pattern (string or regex).
        is_regex: Whether to treat the pattern as a regular expression. Default is False.
        case_sensitive: Whether the search should be case-sensitive. Default is True.
        include_patterns: List of file name patterns to include (e.g., ["*.py", "*.ts"]). Default is ["*"].
        exclude_patterns: List of file name patterns to exclude. Default is empty list.
        context_lines: Number of context lines before and after each match. Default is 10.
        max_results: Maximum number of match blocks to return. Default is 50.
    
    Returns:
        A formatted string containing all matches with their file paths, matched lines, and context.
        Each match block is separated by a delimiter line.
    
    Example:
        result = await run_grep("def main", include_patterns=["*.py"], context_lines=5)
    """
    # 获取仓库根目录
    repo_root = os.getenv("REPO_ROOT") or os.getcwd()
    
    # 处理默认值
    if include_patterns is None:
        include_patterns = ["*"]
    if exclude_patterns is None:
        exclude_patterns = []
    
    # 将列表转换为元组以用于缓存
    include_tuple = tuple(include_patterns)
    exclude_tuple = tuple(exclude_patterns)
    
    # 调用内部函数
    return _grep_internal(
        repo_root=repo_root,
        pattern=pattern,
        is_regex=is_regex,
        case_sensitive=case_sensitive,
        include_patterns=include_tuple,
        exclude_patterns=exclude_tuple,
        context_lines=context_lines,
        max_results=max_results,
    )


class GrepTool(BaseTool):
    """全库搜索工具（BaseTool 实现）。
    
    用于在代码库中搜索字符串或正则表达式，返回带有详细上下文的结构化结果。
    """
    
    workspace_root: Optional[Path] = Field(
        default=None,
        description="Root path of the workspace. If None, uses current working directory or REPO_ROOT env var."
    )
    
    def __init__(self, workspace_root: Optional[Path] = None, **kwargs):
        """初始化全库搜索工具。"""
        if workspace_root is None:
            workspace_root_str = os.getenv("REPO_ROOT") or os.getcwd()
            workspace_root = Path(workspace_root_str)
        super().__init__(
            name="grep",
            description="Search for a string or regex in the codebase. Returns the file path, the specific matched line, and the surrounding code context.",
            workspace_root=workspace_root,
            **kwargs
        )
    
    async def run(
        self,
        pattern: str,
        is_regex: bool = False,
        case_sensitive: bool = True,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        context_lines: int = 10,
        max_results: int = 50,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """执行全库搜索。
        
        Args:
            pattern: 搜索关键词或正则表达式。
            is_regex: 是否启用正则表达式匹配，默认为 False。
            case_sensitive: 是否区分大小写，默认为 True。
            include_patterns: 文件名匹配模式列表（如 ["*.py", "*.ts"]），默认为 ["*"]。
            exclude_patterns: 排除的文件模式列表，默认为空列表。
            context_lines: 返回匹配行前后的上下文行数，默认为 10。
            max_results: 最大返回的匹配块数量，默认为 50。
            **kwargs: 其他参数（未使用）。
        
        Returns:
            包含以下字段的字典：
            - result: 格式化的搜索结果字符串。
            - pattern: 搜索模式。
            - match_count: 匹配块数量（估算）。
            - error: 如果搜索失败，可选的错误消息。
        """
        try:
            repo_root = str(self.workspace_root) if self.workspace_root else os.getcwd()
            
            # 处理默认值
            if include_patterns is None:
                include_patterns = ["*"]
            if exclude_patterns is None:
                exclude_patterns = []
            
            # 将列表转换为元组以用于缓存
            include_tuple = tuple(include_patterns)
            exclude_tuple = tuple(exclude_patterns)
            
            # 调用内部函数
            result = _grep_internal(
                repo_root=repo_root,
                pattern=pattern,
                is_regex=is_regex,
                case_sensitive=case_sensitive,
                include_patterns=include_tuple,
                exclude_patterns=exclude_tuple,
                context_lines=context_lines,
                max_results=max_results,
            )
            
            # 估算匹配块数量（通过分隔符计算）
            match_count = result.count("File:") if result else 0
            
            return {
                "result": result,
                "pattern": pattern,
                "match_count": match_count,
                "error": None
            }
        except Exception as e:
            return {
                "result": "",
                "pattern": pattern,
                "match_count": 0,
                "error": f"Error during grep search: {str(e)}"
            }

