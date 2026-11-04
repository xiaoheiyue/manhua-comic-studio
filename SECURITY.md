# 安全策略

## 报告漏洞

**不要**在公开 Issue 里贴漏洞细节、密钥、日志里的个人信息。

优先用 GitHub 的 [Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)；
如果不可用，发邮件给维护者（见 GitHub 主页），标题以 `[SECURITY]` 开头。

请包含：影响版本、复现步骤、影响范围、你的修复建议（如果有）。我会在 72 小时内确认，7 天内给出处理计划。

## 本项目的安全边界（请先读）

- 这是一个**本地单用户**工具，默认监听 `127.0.0.1`，**没有登录、没有权限模型、没有多租户隔离**。
- `CORSMiddleware` 当前是 `allow_origins=["*"]`，因为前端与后端同源、开发期方便。**不要把服务暴露到公网**；
  如果确实要，请自己在前面加反向代理 + 鉴权，并把 CORS 收紧。
- 服务能读写本机的 `data/`、`outputs/` 与工程目录。路径校验只是防误操作，不是安全沙箱。
- 上传的小说、照片会落盘到 `data/`，不会上传到第三方；但文本分析会把小说内容发给你配置的文本模型服务（默认 DeepSeek）。
  敏感文本请改用本地模型（把 `TEXT_API_HOST` 指到本地 OpenAI 兼容服务）。
- 生成的图片保存在本地；本项目没有遥测、没有回传。

## 密钥

- 密钥只从 `.env` 或系统环境变量读取，`.env` 已被 `.gitignore` 排除。
- 一旦怀疑泄露：立刻在服务商后台吊销并重新生成，再更新 `.env`。
- 不要在 Issue / 截图 / 日志里粘贴完整密钥；日志只打印前缀。

## 依赖

- 依赖全部来自 PyPI，版本范围写在 `requirements.txt`。
- 发现依赖漏洞请按上面的方式报告，不要直接开公开 Issue。
