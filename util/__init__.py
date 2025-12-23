"""Utility modules for code review agent.

This package contains utility functions for:
- Logging: Agent observations and tool results
- Git operations: Repository information and diff generation
- PR processing: Diff file loading and result formatting
- Argument validation: Command line argument validation and diff loading
- Diff parsing: Git diff parsing with line number mapping
"""

from util.logger import save_observations_to_log
from util.git_utils import (
    get_git_info,
    get_git_diff,
    get_changed_files,
    extract_files_from_diff,
    generate_asset_key,
    get_repo_name,
)
from util.pr_utils import (
    load_diff_from_file,
    print_review_results,
)
from util.arg_utils import (
    validate_repo_path,
    load_diff_from_args,
)
from util.diff_utils import (
    parse_diff_with_line_numbers,
    get_file_context_with_line_numbers,
    generate_context_text_for_file,
    FileContext,
)

__all__ = [
    "save_observations_to_log",
    "get_git_info",
    "get_git_diff",
    "get_changed_files",
    "extract_files_from_diff",
    "generate_asset_key",
    "get_repo_name",
    "load_diff_from_file",
    "print_review_results",
    "validate_repo_path",
    "load_diff_from_args",
    "parse_diff_with_line_numbers",
    "get_file_context_with_line_numbers",
    "generate_context_text_for_file",
    "FileContext",
]
