# 贡献指南

感谢你愿意花时间。这个项目是一个人做起来的，最缺的不是想法，是**能复现的问题**和**能合入的小改动**。

## 一、先跑起来

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env      # 填 DEEPSEEK_API_KEY（只跑自检可以不填）
python selftest.py              # 必须全绿
```

没有 GPU / 没有 ComfyUI 也能贡献：`selftest.py` 是离线全链路自检，不调用真实模型。

## 二、提交前

1. `python selftest.py` 通过（exit code 0）。
2. `ruff check --select E9,F63,F7,F82 .` 通过（抓语法错误与未定义名）。
3. 改了配置项，同步改 README 的配置表与 `docs/手册_完整版.md`。
4. 改了行为，在 `CHANGELOG.md` 的 Unreleased 下写一行。

## 三、代码约定

- 注释与文档用中文，变量名用英文。
- 所有可调参数放 `config.py`，不要在业务代码里硬编码；新增配置要有注释。
- 不要提交 `data/`、`outputs/`、密钥、模型权重、小说原文。
- 新增依赖要说明为什么不用标准库/现有依赖；能不加就不加。
- 前端是原生 JS，没有构建步骤，改动直接在 `web/` 下。

## 四、Pull Request

- 一个 PR 只做一件事；大改动先开 Issue 对齐，别闷头写。
- PR 描述里写：改了什么、为什么、怎么验证（命令或截图）。
- 涉及界面的改动请附截图（改前/改后）。
- 我会在 7 天内回复；如果两周没动静，可以直接 @ 我。

## 五、我不太会接受的东西

- 把 FastAPI 换成别的框架、把原生 JS 换成 React 之类的大重构（除非有 Issue 讨论并达成一致）。
- 引入需要云服务的强依赖（本项目的卖点之一是本地优先）。
- 只改措辞、不改行为的"优化"PR（文档错别字除外）。
- 提交包含版权不明素材（画风图、小说、字体、LoRA）的 PR。

## 六、适合第一次贡献的任务

见 [docs/社区/good-first-issues.md](docs/社区/good-first-issues.md)。从"文档纠错 / 报错信息更清楚 / 加一个配置项"开始最稳。

## 七、行为准则

参与本项目即表示同意 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。对事不对人。
