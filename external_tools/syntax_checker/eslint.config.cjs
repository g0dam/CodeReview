/**
 * 代码审查系统的默认 ESLint 配置（仅检测运行时危险问题）
 * 
 * 当项目没有自己的 ESLint 配置文件时，使用此回退配置。
 * 仅启用真正可能导致运行时报错或严重逻辑错误的规则。
 * 
 * ESLint 9.x 扁平配置格式 (CommonJS)
 */

// 构建配置数组，过滤掉 null 条目
const configs = [
  // JavaScript 文件配置
  {
    files: ["**/*.{js,jsx,mjs,cjs}"],
    
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
    },
    
    rules: {
      // 仅保留真正可能导致运行时报错或严重逻辑错误的规则
      "no-undef": "error",               // ReferenceError
      "no-redeclare": "error",           // SyntaxError (strict mode / let/const)
      "no-duplicate-case": "error",      // Logic error in switch
      "no-func-assign": "error",         // TypeError
      "no-invalid-regexp": "error",      // SyntaxError
      "no-obj-calls": "error",           // TypeError (e.g., Math())
      "use-isnan": "error",              // Logical bug (x === NaN always false)
      "valid-typeof": "error",           // Logical bug (typeof x === "nunber")
      "no-inner-declarations": "error",  // SyntaxError in strict mode
      "no-delete-var": "error",          // Invalid in strict mode
    },
  },
  
  // TypeScript 文件配置（仅使用 ESLint 内置规则，不依赖 @typescript-eslint/plugin）
  (() => {
    let tsParser = null;
    
    const resolvePaths = [
      "./node_modules/@typescript-eslint/parser",
      require("path").join(process.cwd(), "node_modules/@typescript-eslint/parser"),
    ];
    
    for (const resolvePath of resolvePaths) {
      try {
        tsParser = require(resolvePath);
        break;
      } catch (e) {
        // Continue
      }
    }
    
    if (!tsParser) {
      try {
        tsParser = require("@typescript-eslint/parser");
      } catch (e) {
        // Parser not available
      }
    }
    
    if (tsParser) {
      return {
        files: ["**/*.{ts,tsx}"],
        
        languageOptions: {
          parser: tsParser,
          ecmaVersion: "latest",
          sourceType: "module",
          parserOptions: {
            ecmaFeatures: {
              jsx: true,
            },
            project: false, // 不依赖 tsconfig.json
          },
        },
        
        rules: {
          // 关闭 ESLint 原生规则中与 TS 冲突或冗余的项
          "no-unused-vars": "off",
          "no-undef": "off",
          "no-redeclare": "off",
          
          // 启用对 .ts 也安全的“运行时危险”规则（ESLint 内置）
          "no-duplicate-case": "error",
          "no-func-assign": "error",
          "no-invalid-regexp": "error",
          "no-obj-calls": "error",
          "use-isnan": "error",
          "valid-typeof": "error",
          "no-inner-declarations": "error",
          "no-delete-var": "error",
        },
      };
    } else {
      return null; // 跳过 TS 配置
    }
  })(),
  
  // 忽略常见目录
  {
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "**/build/**",
      "**/.next/**",
      "**/coverage/**",
      "**/*.min.js",
      "**/vendor/**",
    ],
  },
];

module.exports = configs.filter(config => config !== null);