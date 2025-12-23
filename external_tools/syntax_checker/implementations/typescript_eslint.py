"""TypeScript/JavaScript syntax checker using ESLint.

This module implements a syntax checker for TypeScript and JavaScript files using ESLint,
a popular JavaScript and TypeScript linter.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from external_tools.syntax_checker.base import BaseSyntaxChecker, LintError


class TypeScriptESLintChecker(BaseSyntaxChecker):
    """Syntax checker for TypeScript and JavaScript files using ESLint.
    
    This checker uses the `eslint` command to analyze TypeScript and JavaScript files
    and report linting errors. It gracefully handles cases where ESLint is not installed
    or files don't exist. If the project doesn't have an ESLint configuration file,
    it will use a default configuration provided by the code review system.
    """
    
    def __init__(self, args: Optional[str] = None, use_default_config: bool = True):
        """Initialize the ESLint checker.
        
        Args:
            args: Optional command-line arguments for eslint.
                  Default: "--format json --no-color"
            use_default_config: If True, use default config when project has no ESLint config.
                                Default: True
        """
        self._eslint_available = self._check_eslint_available()
        self.args = args or "--format json --no-color"
        self.use_default_config = use_default_config
        self._default_config_path = self._get_default_config_path()
        self._warning_shown = False
    
    def _get_default_config_path(self) -> Optional[Path]:
        """Get path to the default ESLint configuration file.
        
        Returns:
            Path to default config file, or None if not found.
        """
        # Default config is in the syntax_checker directory
        # Try .cjs first (CommonJS, more compatible), then .js
        config_dir = Path(__file__).parent.parent
        default_config_cjs = config_dir / "eslint.config.cjs"
        default_config_js = config_dir / "eslint.config.js"
        
        if default_config_cjs.exists():
            return default_config_cjs
        if default_config_js.exists():
            return default_config_js
        return None
    
    def _has_project_config(self, repo_path: Path) -> bool:
        """Check if the project has its own ESLint configuration file.
        
        Args:
            repo_path: Root path of the repository.
        
        Returns:
            True if project has ESLint config, False otherwise.
        """
        # ESLint 9.x looks for these config files
        config_files = [
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.cjs",
        ]
        
        for config_file in config_files:
            if (repo_path / config_file).exists():
                return True
        
        return False
    
    def _check_eslint_available(self) -> bool:
        """Check if eslint command is available in PATH.
        
        Returns:
            True if eslint is available, False otherwise.
        """
        return shutil.which("eslint") is not None
    
    async def check(
        self,
        repo_path: Path,
        files: List[str]
    ) -> List[LintError]:
        """Run ESLint on the specified TypeScript/JavaScript files.
        
        Args:
            repo_path: Root path of the repository.
            files: List of file paths relative to repo_path to check.
        
        Returns:
            A list of LintError objects found by ESLint. Returns empty list
            if ESLint is not available, if no TypeScript/JavaScript files are found, or if
            no errors are detected.
        """
        if not self._eslint_available:
            if not self._warning_shown:
                print("  ⚠️  Warning: ESLint is not installed. TypeScript/JavaScript syntax checking will be skipped.")
                print("     Install ESLint with: npm install -g eslint")
                self._warning_shown = True
            return []
        
        # Filter to only TypeScript/JavaScript files and existing files
        ts_js_files = [
            f for f in files
            if f.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
        ]
        
        if not ts_js_files:
            return []
        
        # Get existing file paths
        existing_files = self._filter_existing_files(repo_path, ts_js_files)
        
        if not existing_files:
            # If using --diff-file mode, files might not exist locally
            # Return empty list gracefully
            return []
        
        # Build eslint command
        # Use relative paths from repo_path
        relative_paths = [str(f.relative_to(repo_path)) for f in existing_files]
        
        try:
            # Check if project has its own ESLint config
            has_project_config = self._has_project_config(repo_path)
            
            # Parse args string into list
            args_list = self.args.split() if isinstance(self.args, str) else []
            
            # If project doesn't have config and we have a default config, use it
            if not has_project_config and self.use_default_config and self._default_config_path:
                # Use absolute path to default config
                config_path = str(self._default_config_path.resolve())
                args_list.extend(["--config", config_path])
            
            cmd = [
                "eslint",
                *args_list,
                *relative_paths
            ]
            
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=False,  # Don't raise on non-zero exit
                encoding="utf-8"
            )
            
            # ESLint returns non-zero exit code if errors are found
            # Exit codes: 0 = no errors, 1 = errors found, 2 = fatal error
            # We check for actual failures (exit code 2 indicates a fatal error in eslint itself)
            if result.returncode == 2:
                # Exit code 2 indicates a fatal error in eslint itself
                # This could be due to config issues, but we've already tried to use default config
                # if needed, so just return empty list gracefully
                return []
            
            # Parse JSON output
            if not result.stdout.strip():
                return []
            
            errors = []
            stdout = result.stdout.strip()
            
            # ESLint outputs JSON array
            try:
                diagnostics = json.loads(stdout)
                if not isinstance(diagnostics, list):
                    diagnostics = [diagnostics]
            except json.JSONDecodeError:
                # If JSON parsing fails, try to parse text output as fallback
                # (though this shouldn't happen with --format json)
                return []
            
            # Process each diagnostic
            for data in diagnostics:
                if not isinstance(data, dict):
                    continue
                
                # ESLint JSON format:
                # {
                #   "filePath": "path/to/file.ts",
                #   "messages": [
                #     {
                #       "ruleId": "no-unused-vars",
                #       "severity": 2,
                #       "message": "Unexpected console statement.",
                #       "line": 10,
                #       "column": 5,
                #       "endLine": 10,
                #       "endColumn": 15
                #     }
                #   ],
                #   "errorCount": 1,
                #   "warningCount": 0
                # }
                
                # Extract file path
                file_path_str = data.get("filePath", "")
                if not file_path_str:
                    continue
                
                # Get relative path from repo_path
                file_path = Path(file_path_str)
                if file_path.is_absolute():
                    try:
                        file_path = file_path.relative_to(repo_path)
                    except ValueError:
                        # File is outside repo, skip
                        continue
                
                # Process messages for this file
                messages = data.get("messages", [])
                for msg in messages:
                    if not isinstance(msg, dict):
                        continue
                    
                    line_num = msg.get("line", 1)
                    message = msg.get("message", "")
                    rule_id = msg.get("ruleId", "")
                    
                    # Filter out configuration errors (not code errors)
                    # These are ESLint config issues, not actual code problems
                    message_lower = message.lower()
                    is_config_error = (
                        not rule_id  # No rule ID usually means config/fatal error
                        or "was not found" in message_lower
                        or "definition for rule" in message_lower
                        or "cannot read" in message_lower
                        or "plugin" in message_lower and ("not found" in message_lower or "cannot find" in message_lower)
                        or (rule_id and rule_id.startswith("@") and "not found" in message_lower)
                    )
                    
                    if is_config_error:
                        # Skip configuration errors like:
                        # - "Definition for rule '@typescript-eslint/no-unused-vars' was not found"
                        # - "Plugin '@typescript-eslint' was not found"
                        # - Other ESLint configuration issues
                        continue
                    
                    # Determine severity from ESLint severity field
                    # ESLint severity: 0 = off, 1 = warning, 2 = error
                    eslint_severity = msg.get("severity", 2)
                    if eslint_severity == 2:
                        severity = "error"
                    elif eslint_severity == 1:
                        severity = "warning"
                    else:
                        severity = "info"
                    
                    errors.append(LintError(
                        file=str(file_path),
                        line=line_num,
                        message=message,
                        severity=severity,
                        code=rule_id
                    ))
            
            return errors
        
        except Exception:
            # Gracefully handle any errors (subprocess failures, etc.)
            return []
    
    def get_supported_extensions(self) -> List[str]:
        """Get list of file extensions this checker supports.
        
        Returns:
            List of TypeScript/JavaScript file extensions: [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"].
        """
        return [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]
