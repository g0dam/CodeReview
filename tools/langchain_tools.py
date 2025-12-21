"""LangChain 标准工具定义。

重构说明：
- 使用 LangChain 的 @tool 装饰器定义工具（LangGraph 标准做法）
- 工具必须包含详细的 docstring（用于 LLM 意图识别）和类型注解
- 这些工具可以直接使用 llm.bind_tools() 绑定到模型
"""

from pathlib import Path
from typing import Optional, Dict, Any
from langchain_core.tools import tool
from dao.factory import get_storage


@tool
async def fetch_repo_map(asset_key: Optional[str] = None) -> Dict[str, Any]:
    """获取仓库结构映射。
    
    此工具从存储层加载仓库映射资产，返回项目结构的摘要字符串。
    代理可以使用此工具来"感知"代码库结构，而不是依赖硬编码的上下文。
    
    Args:
        asset_key: 仓库映射的资产键。如果为 None，则使用默认的 'repo_map'。
    
    Returns:
        包含以下字段的字典：
        - summary: 仓库结构的摘要字符串
        - file_count: 仓库中的文件数量
        - files: 文件路径列表（如果太多则截断）
        - error: 如果获取失败，可选的错误消息
    
    Example:
        result = await fetch_repo_map(asset_key="my_repo")
        print(result["summary"])
    """
    try:
        storage = get_storage()
        await storage.connect()
        
        # 使用 asset_key 如果设置了，否则回退到 "repo_map" 以保持向后兼容
        key = asset_key if asset_key else "repo_map"
        repo_map_data = await storage.load("assets", key)
        
        if repo_map_data is None:
            return {
                "summary": "Repository map not found. Please build the repository map first.",
                "file_count": 0,
                "files": [],
                "error": "Repository map not found in storage"
            }
        
        # 提取关键信息
        file_tree = repo_map_data.get("file_tree", "No file tree available")
        file_count = repo_map_data.get("file_count", 0)
        files = repo_map_data.get("files", [])
        source_path = repo_map_data.get("source_path", "unknown")
        
        # 创建摘要字符串
        # 限制文件列表为前 50 个以提高可读性
        files_preview = files[:50]
        files_display = "\n".join(f"  - {f}" for f in files_preview)
        if len(files) > 50:
            files_display += f"\n  ... and {len(files) - 50} more files"
        
        summary = f"""Repository Structure Summary:
            Source Path: {source_path}
            Total Files: {file_count}

            File Tree:
            {file_tree}

            Key Files (first 50):
            {files_display}
            """
        
        return {
            "summary": summary,
            "file_count": file_count,
            "files": files_preview,  # 仅返回预览
            "all_files": files,  # 如果需要，完整列表可用
            "source_path": source_path,
            "error": None
        }
    except Exception as e:
        return {
            "summary": "",
            "file_count": 0,
            "files": [],
            "error": f"Error fetching repository map: {str(e)}"
        }


@tool
async def read_file(
    file_path: str,
    workspace_root: Optional[str] = None,
    max_lines: Optional[int] = None,
    encoding: str = "utf-8"
) -> Dict[str, Any]:
    """读取文件内容。
    
    此工具读取文件的内容并返回其内容和元数据。
    代理在审查过程中使用此工具来检查代码文件。
    
    Args:
        file_path: 要读取的文件路径（相对于工作区根目录或绝对路径）。
        workspace_root: 工作区根目录路径。如果为 None，使用当前工作目录。
        max_lines: 可选的最大行数限制。如果文件超过此限制，将截断内容。
        encoding: 文件编码，默认为 'utf-8'。
    
    Returns:
        包含以下字段的字典：
        - content: 文件内容字符串
        - file_path: 解析后的文件路径
        - line_count: 文件中的行数
        - encoding: 使用的编码
        - error: 如果读取失败，可选的错误消息
    
    Example:
        result = await read_file("src/main.py", workspace_root="/path/to/repo")
        print(result["content"])
    """
    try:
        file_path_obj = Path(file_path)
        if not file_path_obj.is_absolute():
            # 相对于工作区根目录解析
            workspace = Path(workspace_root) if workspace_root else Path.cwd()
            file_path_obj = workspace / file_path_obj
        
        if not file_path_obj.exists():
            return {
                "content": "",
                "file_path": str(file_path_obj),
                "line_count": 0,
                "encoding": encoding,
                "error": f"File not found: {file_path_obj}"
            }
        
        with open(file_path_obj, "r", encoding=encoding) as f:
            lines = f.readlines()
            line_count = len(lines)
            
            if max_lines and line_count > max_lines:
                content = "".join(lines[:max_lines])
                content += f"\n... (truncated, {line_count - max_lines} more lines)"
            else:
                content = "".join(lines)
        
        return {
            "content": content,
            "file_path": str(file_path_obj),
            "line_count": line_count,
            "encoding": encoding,
            "error": None
        }
    except Exception as e:
        return {
            "content": "",
            "file_path": str(file_path),
            "line_count": 0,
            "encoding": encoding,
            "error": f"Error reading file: {str(e)}"
        }


def create_tools_with_context(
    workspace_root: Optional[Path] = None,
    asset_key: Optional[str] = None
) -> list:
    """创建带有上下文的工具列表。
    
    此函数创建工具实例，并为需要上下文的工具（如 read_file）注入配置。
    由于 @tool 装饰器返回的是 Tool 对象，我们需要创建新的工具函数来注入上下文。
    
    重构说明：
    - 为了支持依赖注入（workspace_root, asset_key），我们需要创建新的工具函数
    - 使用 @tool 装饰器创建新的工具，内部调用原始工具逻辑
    - 这是 LangGraph 中处理工具上下文的标准做法
    
    Args:
        workspace_root: 工作区根目录路径。
        asset_key: 仓库映射的资产键。
    
    Returns:
        工具列表，可以直接用于 llm.bind_tools()。
    """
    # 重构说明：由于 @tool 装饰器返回的是 Tool 对象，不能使用 partial
    # 我们需要创建新的工具函数，内部调用原始工具逻辑
    
    @tool
    async def fetch_repo_map_with_context() -> Dict[str, Any]:
        """获取仓库结构映射（带上下文）。
        
        此工具从存储层加载仓库映射资产，使用配置的 asset_key。
        
        Returns:
            包含 summary, file_count, files, error 的字典。
        """
        # 调用原始工具函数，传入 asset_key
        return await fetch_repo_map(asset_key=asset_key)
    
    @tool
    async def read_file_with_context(
        file_path: str,
        max_lines: Optional[int] = None,
        encoding: str = "utf-8"
    ) -> Dict[str, Any]:
        """读取文件内容（带上下文）。
        
        此工具读取文件的内容，使用配置的 workspace_root。
        
        Args:
            file_path: 要读取的文件路径（相对于工作区根目录或绝对路径）。
            max_lines: 可选的最大行数限制。
            encoding: 文件编码，默认为 'utf-8'。
        
        Returns:
            包含 content, file_path, line_count, encoding, error 的字典。
        """
        # 调用原始工具函数，传入 workspace_root
        workspace_root_str = str(workspace_root) if workspace_root else None
        return await read_file(
            file_path=file_path,
            workspace_root=workspace_root_str,
            max_lines=max_lines,
            encoding=encoding
        )
    
    return [
        fetch_repo_map_with_context,
        read_file_with_context
    ]
