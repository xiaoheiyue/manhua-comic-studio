# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号用 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- `scripts/download_models.py`：走 ModelScope 魔搭国内直连下载 7 个权重，断点续传 + SHA256 校验，不需要代理
- `models/manifest.json` 增加 `download` 字段（镜像地址）与 `mirrors` 说明
- README 新增「不止漫画：创作工坊」展示（海报 / 表情包 / 信息图 / 插画 / IP / 电商图）
- `docs/assets/showcase/`：创作工坊成品图与界面截图

## [0.1.0] - 2026-11-04

首个公开版本。整理自本地自用版本，功能与内部版本一致，主要变化在"能不能被别人装起来"。

### 新增

- `LICENSE`（Apache-2.0）、`NOTICE`、`THIRD_PARTY.md`，补齐 handraw-style 署名
- `.env` 机制：密钥只从环境变量 / `.env` 读取，源码不再包含密钥
- `scripts/fetch_handraw_style.ps1` / `.sh`：一键拉取风格库（400MB，不随仓库提交）
- `docs/模型与依赖清单.md`：模型文件、目录、下载地址与许可
- CI（ruff 语法检查 + 离线 selftest）、Issue / PR 模板、`CONTRIBUTING.md`、`ROADMAP.md`
- `docs/社区/`：内测群公告、招募帖、发布帖、环境问卷

### 修复

- `requirements.txt` 中一行被字面 `\n` 吞掉，导致 `scipy / mediapipe / opencv-python` 实际未被安装
- `.gitignore` 的 `data/`、`screenshots/` 未锚定根目录，误伤 `rules/data/*.json`（程序真正读取的规则数据）
  与 `docs/assets/screenshots/*.png`（README 配图），干净 clone 会缺版式/规则
- `data/pose_library/`（骨架库，运行与自检必需）未随仓库分发
- `comicgen/pipeline.py` 使用 `list[Any]` 但未导入 `Any`（ruff F821）
- 文档中指向已移动文件的链接（需求说明书、条漫规则）

### 变更

- 根目录 148 个一次性探针脚本移入 `archive/`（不进仓库）
- 旧 README（71KB）移入 `docs/手册_完整版.md`，根 README 重写为上手向
- 运行期数据、产物、参考资料、模型权重、样例小说统一 `.gitignore`

### 已知问题

- 角色同脸不稳定（见 README「已知边界」）
- 尚无自动化测试框架，只有 `selftest.py` 脚本级自检
