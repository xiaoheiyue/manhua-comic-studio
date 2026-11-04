# Good First Issues

给第一次贡献的人。每条都写了「改哪里 / 怎么验收」，照着做就能合。

> 认领方式：在对应 Issue 下留言「我试试」，我把它 assign 给你。超过两周没动静会自动释放。

## 文档类（不需要跑起来）

1. **README 英文版**
   - 改哪里：新增 `README.en.md`，对照 `README.md` 翻译；顶部加语言切换。
   - 验收：链接可点、截图路径正确、不机翻味。

2. **常见报错对照表**
   - 改哪里：`docs/常见报错.md`（新建），从 `docs/手册_完整版.md` 的「常见问题」里抽 15 条。
   - 验收：每条都有「现象 → 原因 → 解决」三行。

3. **配置项速查表**
   - 改哪里：`docs/配置速查.md`（新建），把 `config.py` 里的顶层配置按模块分组列出默认值。
   - 验收：与 `config.py` 一一对应，脚本可校验（可选）。

## 代码类（小改动）

4. **`requirements.txt` 兼容性检查**
   - 改哪里：`scripts/check_env.py`（新建），检查 Python 版本、关键包、ComfyUI 端口、模型文件是否存在。
   - 验收：缺什么就打印「缺 X，怎么装」，退出码非 0。

5. **日志脱敏**
   - 改哪里：`comicgen/textclient.py` 与 `config.py` 的日志输出。
   - 验收：任何日志都不出现完整 key（只留前缀 4 位）。

6. **分页版式的单元测试**
   - 改哪里：新建 `tests/test_pagelayout.py`，覆盖 `comicgen/pagelayout.py` 的版式选择与格数约束。
   - 验收：`python -m pytest tests/` 通过；不依赖 GPU。

7. **支持 `.md` / `.docx` 输入**
   - 改哪里：`server.py` 的 `/api/text/upload`，复用 `extract_docx.py` 的解析逻辑。
   - 验收：上传 `.md` 能正确提取正文；有测试文本。

8. **`--port` / `--host` 启动参数**
   - 改哪里：`server.py` 入口，`config.WEB_PORT` 允许命令行覆盖。
   - 验收：`python server.py --port 7871` 生效，README 同步。

## 前端类

9. **工程列表搜索与排序**
   - 改哪里：`web/app.js` 的工程列表渲染；`web/index.html` 加搜索框。
   - 验收：按标题/时间过滤；刷新后保持。

10. **气泡调试视图**
    - 改哪里：`web/app.js` 生成页，增加「显示气泡计划 / 定位框」开关。
    - 验收：能看到 `plan / debug / layouts` 的叠加层，不影响出图。

## 硬件 / 兼容类

11. **Linux 安装脚本**
    - 改哪里：新增 `scripts/install.sh`（建 venv、装依赖、装 Noto CJK、拉风格库）。
    - 验收：Ubuntu 22.04 干净环境跑通 `selftest.py`。

12. **Apple Silicon 说明**
    - 改哪里：`docs/模型与依赖清单.md` 增补 MPS 后端注意事项。
    - 验收：写清哪些能跑、哪些不能、速度预期。

---

## 维护者自用：怎么把 Issue 标成 good first issue

- 标题写清楚「做什么 + 在哪」；
- 正文给「验收标准」和「可能踩的坑」；
- 加 `good first issue` + `help wanted` 标签；
- 预期耗时写在开头（半小时 / 半天）。
