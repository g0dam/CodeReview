"""Git Diff 解析工具，用于生成带绝对行号的代码上下文。

此模块使用 unidiff 库解析 Git diff 内容，生成新文件（HEAD 版本）中带绝对行号的代码上下文。
这对于可以引用目标文件中特定行的准确代码审查注释至关重要。
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unidiff import PatchSet

logger = logging.getLogger(__name__)


def parse_diff_with_line_numbers(diff_content: str) -> Dict[str, "FileContext"]:
    """解析 Git diff 并生成带绝对行号的代码上下文。
    
    此函数解析 Git diff 字符串，生成从文件路径到 FileContext 对象的映射，
    FileContext 包含新文件（HEAD 版本）中带绝对行号的代码。
    """
    if not diff_content or not diff_content.strip():
        return {}
    
    try:
        patch_set = PatchSet(diff_content)
    except Exception as e:
        logger.error(f"Failed to parse diff content: {e}")
        return {}
    
    file_contexts: Dict[str, FileContext] = {}
    
    for patched_file in patch_set:
        file_path = _normalize_file_path(patched_file.path)
        
        # Skip binary files and deleted files (no new file content)
        if patched_file.is_binary_file or patched_file.is_removed_file:
            logger.debug(f"Skipping binary/removed file: {file_path}")
            continue
        
        # Build line-by-line context for the new file
        new_file_lines: List[Tuple[int, str]] = []
        added_lines: set[int] = set()
        modified_lines: set[int] = set()
        removed_lines: set[int] = set()
        
        # Track line numbers in new file
        # We need to manually track because unidiff may not provide target_line_no for all lines
        current_new_line = 1
        
        for hunk in patched_file:
            # Get the starting line number in the new file from hunk header
            hunk_new_start = hunk.target_start
            
            # If there's a gap before this hunk, we can't include those lines
            # (they're not in the diff), but we note the position
            if current_new_line < hunk_new_start:
                current_new_line = hunk_new_start
            
            # Process each line in the hunk
            for line in hunk:
                if line.is_added:
                    # Added line: appears in new file
                    # Use target_line_no if available, otherwise use current position
                    line_num = getattr(line, 'target_line_no', None) or current_new_line
                    new_file_lines.append((line_num, line.value))
                    added_lines.add(line_num)
                    current_new_line += 1
                elif line.is_removed:
                    # Removed line: doesn't appear in new file
                    removed_lines.add(line.source_line_no)
                    # Don't increment current_new_line
                elif line.is_context:
                    # Context line: exists in both old and new files
                    # Use target_line_no if available, otherwise use current position
                    line_num = getattr(line, 'target_line_no', None) or current_new_line
                    new_file_lines.append((line_num, line.value))
                    current_new_line += 1
                else:
                    # Unknown line type, skip
                    logger.debug(f"Unknown line type in hunk: {line}")
            
            # Detect modified lines: consecutive removed+added pairs at similar positions
            # This is a heuristic since unified diff doesn't explicitly mark modifications
            hunk_lines = list(hunk)
            for i in range(len(hunk_lines) - 1):
                if hunk_lines[i].is_removed and hunk_lines[i + 1].is_added:
                    # Check if they're at similar positions (likely a modification)
                    removed_pos = hunk_lines[i].source_line_no
                    added_pos = getattr(hunk_lines[i + 1], 'target_line_no', None)
                    if added_pos and abs(added_pos - removed_pos) <= 3:
                        modified_lines.add(added_pos)
        
        # Sort lines by line number to ensure correct order
        new_file_lines.sort(key=lambda x: x[0])
        
        # Generate formatted context text
        context_text = _format_context_text(
            file_path=file_path,
            new_file_lines=new_file_lines,
            added_lines=added_lines,
            modified_lines=modified_lines
        )
        
        file_contexts[file_path] = FileContext(
            file_path=file_path,
            new_file_lines=new_file_lines,
            context_text=context_text,
            added_lines=added_lines,
            modified_lines=modified_lines,
            removed_lines=removed_lines
        )
    
    return file_contexts


def get_file_context_with_line_numbers(
    diff_content: str,
    file_path: str
) -> Optional["FileContext"]:
    """Get code context with line numbers for a specific file from diff.
    
    Args:
        diff_content: The Git diff content as a string.
        file_path: The file path to extract (relative to repo root).
    
    Returns:
        FileContext object for the specified file, or None if not found.
    """
    all_contexts = parse_diff_with_line_numbers(diff_content)
    
    # Try exact match first
    if file_path in all_contexts:
        return all_contexts[file_path]
    
    # Try normalized path
    normalized_path = _normalize_file_path(file_path)
    if normalized_path in all_contexts:
        return all_contexts[normalized_path]
    
    # Try reverse lookup (in case paths are stored differently)
    for stored_path, context in all_contexts.items():
        if _normalize_file_path(stored_path) == normalized_path:
            return context
    
    return None


def generate_context_text_for_file(
    diff_content: str,
    file_path: str,
    include_context_lines: bool = True,
    max_context_lines: int = 5
) -> str:
    """Generate formatted code context text for a specific file.
    
    This is a convenience function that extracts the context text for a file
    from the diff. It's designed to be used in LLM prompts.
    
    Args:
        diff_content: The Git diff content as a string.
        file_path: The file path to extract (relative to repo root).
        include_context_lines: Whether to include surrounding context lines.
        max_context_lines: Maximum number of context lines to include around changes.
    
    Returns:
        Formatted text with line numbers, ready for LLM consumption.
        Returns empty string if file not found in diff.
    """
    context = get_file_context_with_line_numbers(diff_content, file_path)
    if not context:
        return ""
    
    if include_context_lines:
        return context.context_text
    else:
        # Return only changed lines
        changed_lines = []
        for line_num, line_content in context.new_file_lines:
            if line_num in context.added_lines or line_num in context.modified_lines:
                changed_lines.append((line_num, line_content))
        
        return _format_context_text(
            file_path=file_path,
            new_file_lines=changed_lines,
            added_lines=context.added_lines,
            modified_lines=context.modified_lines
        )


def _normalize_file_path(file_path: str) -> str:
    """Normalize file path by removing 'a/' or 'b/' prefixes.
    
    Args:
        file_path: File path from diff (may have 'a/' or 'b/' prefix).
    
    Returns:
        Normalized file path without prefix.
    """
    path = file_path.strip()
    # Remove 'a/' or 'b/' prefix if present
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    # Remove leading slash
    if path.startswith("/"):
        path = path[1:]
    return path


def _format_context_text(
    file_path: str,
    new_file_lines: List[Tuple[int, str]],
    added_lines: set[int],
    modified_lines: set[int]
) -> str:
    """Format code context text with line numbers for LLM consumption.
    
    Args:
        file_path: The file path.
        new_file_lines: List of (line_number, line_content) tuples.
        added_lines: Set of line numbers that were added.
        modified_lines: Set of line numbers that were modified.
    
    Returns:
        Formatted text with line numbers and change markers.
    """
    if not new_file_lines:
        return f"File: {file_path}\n(No new content in this file)\n"
    
    lines = [f"File: {file_path}", "=" * 80]
    
    for line_num, line_content in new_file_lines:
        # Determine change type marker
        if line_num in added_lines:
            marker = "+"  # Added line
        elif line_num in modified_lines:
            marker = "~"  # Modified line
        else:
            marker = " "  # Context line
        
        # Format: [marker] line_number: content
        # Remove trailing newline from line_content if present
        content = line_content.rstrip("\n\r")
        lines.append(f"{marker} {line_num:4d}: {content}")
    
    lines.append("=" * 80)
    return "\n".join(lines)


class FileContext:
    """Represents code context for a file with line number information.
    
    Attributes:
        file_path: The file path (relative to repo root).
        new_file_lines: List of (line_number, line_content) tuples for new file.
        context_text: Formatted text with line numbers for LLM consumption.
        added_lines: Set of line numbers that were added in the new file.
        modified_lines: Set of line numbers that were modified.
        removed_lines: Set of line numbers that were removed (in old file).
    """
    
    def __init__(
        self,
        file_path: str,
        new_file_lines: List[Tuple[int, str]],
        context_text: str,
        added_lines: set[int],
        modified_lines: set[int],
        removed_lines: set[int]
    ):
        """Initialize FileContext.
        
        Args:
            file_path: The file path (relative to repo root).
            new_file_lines: List of (line_number, line_content) tuples.
            context_text: Formatted text with line numbers.
            added_lines: Set of line numbers that were added.
            modified_lines: Set of line numbers that were modified.
            removed_lines: Set of line numbers that were removed.
        """
        self.file_path = file_path
        self.new_file_lines = new_file_lines
        self.context_text = context_text
        self.added_lines = added_lines
        self.modified_lines = modified_lines
        self.removed_lines = removed_lines
    
    def get_line_content(self, line_number: int) -> Optional[str]:
        """Get the content of a specific line number in the new file.
        
        Args:
            line_number: The line number in the new file (1-based).
        
        Returns:
            The line content, or None if line number not found.
        """
        for line_num, line_content in self.new_file_lines:
            if line_num == line_number:
                return line_content.rstrip("\n\r")
        return None
    
    def is_line_changed(self, line_number: int) -> bool:
        """Check if a line number was changed (added or modified).
        
        Args:
            line_number: The line number in the new file (1-based).
        
        Returns:
            True if the line was added or modified, False otherwise.
        """
        return line_number in self.added_lines or line_number in self.modified_lines


def extract_file_diff(diff_context: str, file_path: str) -> str:
    """提取指定文件的 diff 片段（包含绝对行号）。
    
    使用 unidiff 解析 Git diff，生成包含新文件绝对行号的代码上下文。
    如果解析失败，回退到原始 diff 片段。
    
    Args:
        diff_context: Git diff 内容字符串。
        file_path: 要提取的文件路径（相对于仓库根目录）。
    
    Returns:
        格式化的代码上下文文本（带行号），或原始 diff 片段。
    """
    try:
        # Use diff_utils to generate context with line numbers
        context_text = generate_context_text_for_file(
            diff_content=diff_context,
            file_path=file_path,
            include_context_lines=True,
            max_context_lines=5
        )
        
        if context_text:
            return context_text
        else:
            # If no context found, fall back to raw diff extraction
            logger.debug(f"Could not generate context with line numbers for {file_path}, falling back to raw diff")
    except Exception as e:
        # If parsing fails, fall back to raw diff extraction
        logger.warning(f"Failed to parse diff with line numbers for {file_path}: {e}, falling back to raw diff")
    
    # Fallback: Extract raw diff section using regex (original behavior)
    import re
    patterns = [
        rf"diff --git.*{re.escape(file_path)}.*?\n(.*?)(?=\ndiff --git|\Z)",
        rf"--- a/{re.escape(file_path)}.*?\n(.*?)(?=\n--- a/|\Z)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, diff_context, re.DOTALL)
        if match:
            return match.group(0)
    
    # If no specific section found, return a portion of the diff
    return diff_context[:3000] if diff_context else ""

