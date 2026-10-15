# 榆林有志 · 方志识别与讲解智能体

榆林地方志整理与历史展陈项目。首页以古代大漠、民国灰度、现代城市与生态建设串起历史长卷；方志库展示真实材料、原页与识别成果。识别助手生成待审原文与白话初译，讲解助手调用自有资料库，回答附有来源和原页链接。

成果数量以数据库为准，机器识别、智能体转录、人工校勘与发布分别记录。已完成本地模型联调；尚未部署到公网。缺少模型配置时显示明确错误，不用模拟回答代替真实结果。

## 启动

公开源码不包含真实密钥、本地数据库、原始扫描件、识别草稿、运行记录及本机备份。环境模板中的密钥留空；下载后按下方步骤初始化环境，并自行获取和导入材料。当前机器上的成果数量不代表全新安装后的数据量。

Windows 最简单的方式：双击项目根目录的 **`启动.bat`**。脚本会检查依赖、启动后端 API 与前端页面，并在前端就绪后自动打开历史长卷首页（默认 `http://127.0.0.1:5173/`，默认端口被占用时自动顺延）。

脚本从自身目录启动，支持路径包含空格；报错后保留窗口。重复双击会打开同一项目已就绪的网页，正在启动时不会再建一个数据库工作进程。本地前端默认使用 Node 模式。

仅检查环境和端口配置：在项目目录运行 `启动.bat --check`，成功返回 0；需要关闭浏览器自动打开时运行 `启动.bat --no-open`。自动化调用可设置 `YULIN_NO_PAUSE=1`，让脚本结束时不等待按键。若启动失败，请查看窗口中第一条错误，再运行 `--check`；首次前端编译可能需要等待，180 秒仍未就绪会退出并保留错误。

其他方式：

```bash
# Windows（PowerShell／Git Bash）
.venv\Scripts\python.exe scripts/dev.py   # Git Bash: .venv/Scripts/python.exe scripts/dev.py
```

macOS／Linux：

```bash
.venv/bin/python scripts/dev.py
```

访问启动脚本打印的地址，默认 `http://127.0.0.1:5173/`；接口文档位于 `http://127.0.0.1:8765/docs`。按 Ctrl-C 停止两项服务。仅监听本机，不是公网部署。默认端口被其他程序（包括其他项目）占用时，脚本自动顺延到下一个空闲端口并打印实际地址，不会结束任何程序；也可以用环境变量显式指定，例如 `$env:API_PORT="8766"; $env:WEB_PORT="5174"; .venv\Scripts\python.exe scripts/dev.py`（PowerShell）（显式指定的端口被占用时会直接报错）。

全新环境（推荐 Python 3.13、Node.js 24；Windows 下 Python 3.12 亦可）：

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt   # POSIX: .venv/bin/pip
npm --prefix apps/web ci
.venv/Scripts/python scripts/init_local.py
.venv/Scripts/python scripts/import_sources.py
.venv/Scripts/python scripts/seed_timeline.py
.venv/Scripts/python scripts/dev.py
```

下载材料不随 Git 提交；如果当前机器没有 `data/raw/`，先按下方材料说明获取，不要将缺失文件的导入错误理解为已收录。

## 接入 GLM

编辑项目根目录的 `.env`：

```dotenv
GLM_API_KEY=你的智谱API密钥
GLM_TEXT_MODEL=glm-4-flash
VISION_MODEL=glm-4.6v-flash
```

`glm-4-flash` 用于工具规划、白话初译和讲解；它不是图像模型。识别默认使用独立配置的 `glm-4.6v-flash`。视觉服务默认复用 `GLM_API_KEY`，也可单独填写 `VISION_API_KEY`、`VISION_BASE_URL`、`VISION_MODEL` 接入其他兼容服务。模型名称、免费策略、账号可用性与限流以智谱控制台为准，程序不会自动转用付费模型。

首次初始化已随机生成 `ADMIN_TOKEN`，保存在权限为 600 的 `.env`。在“智能体工作台”中填写该令牌，选择材料与页码，执行“识别并生成初译草稿”。检查后发布为 AI 初译，或勾选已完成人工核对后发布为校勘版本。修改 `.env` 后重启服务。

操作顺序：文献库核对原页 → 工作台识别 → 读取草稿 → 审校／发布 → 阅读器向讲解助手提问 → 点击引用回到对应文本版本和原页。

## 运行台（导入 · 识别 · 去重）

`/workbench/ops` 是本机整理者的统一入口，三个标签页：

- **导入材料**：上传 PDF/JPEG/PNG，填写 edition_id、书名、来源卷名与核验卷名、刊行年、来源 URL、许可状态。按文件头判断真实格式（不信任扩展名），按 SHA-256 去重（同内容不重复计数），待核许可的材料不入公开。
- **识别县志**：选择材料与页范围批量识别，逐页提交并等待上一页结束，可跳过已有草稿页，可停止；显示逐页状态与原页缩略图、真实事件流。模型服务异常时给出明确提示并按类型退避，不偷偷重发。单页识别在 `/workbench/dev`。
- **重复页去重**：展示同内容共享页、同名/近似文本的候选对；可用文本比对与模型判定给出建议，由你确认“合并/不重复”。合并不删除任何文本，只把重复页指向规范页。

接口对应：`POST /admin/imports`、`GET /admin/imports`、`GET /admin/editions`、`GET /admin/dedup`、`GET /admin/dedup/stats`、`POST /admin/dedup/judgements`、`POST /admin/dedup/decisions`。

## 检索与历史长卷

- 首页 `/` 默认展示 24 个关键节点，另外 52 条编年可展开；检索与筛选覆盖完整资料。土路、石路、马路沿同一条连续路径逐渐变化，生态建设直接放在现代时间线上，以“仍在路上”与城市画卷结尾。每个大节点可查看讲解。
- 文献库 `/library` 展示真实目录与识别进度，支持检索与状态筛选。问答检索同时考虑已发布文本、符合公开权限的识别初稿、目录和编年；尚未识别的原页可按需临时转录，并明确说明状态。
- `/timeline` 保留编年列表和管理入口；节点保留依据与来源。阅读器可查看原页及曾发布过的文本版本，旧回答引用可以回溯。
- 展陈使用旧纸、朱红、暗金、沙漠建筑与绿色城市意象，自托管 Noto 中文字体；全站配朱砂笔尖与印章光标。设计、图片来源及实测记录见 [页面说明](docs/heritage-redesign.md)。

## 文件与目录

- `backend/app/agents.py`：两个有界的模型工具调用循环，非预录动画。
- `backend/app/provider.py`：智谱兼容 API、每日调用预算、用量与错误记录。
- `backend/app/repository.py`：真实统计、草稿、发布、SQLite FTS5 中文字符检索、历史长卷节点。
- `backend/app/imports.py`：材料导入（格式校验、哈希去重、版本/页登记），HTTP 与脚本共用。
- `backend/app/dedup.py`：重复页检测与合并（内容哈希 + 文本比对 + 可选模型判定）。
- `backend/app/worker.py`：单消费者持久任务队列（识别/讲解/去重判定），重启时标记中断，不自动重复计费。
- `backend/app/documents.py`：PDF 文字层检查、原页渲染、图像样本读取。
- `启动.bat`：Windows 一键启动（检查依赖、启动前后端、自动打开历史长卷首页）。
- `apps/web/`：React／TypeScript／Vinext 前端，支持本地 Node 开发与生产构建。
- `data/raw/`：下载的原始文件与标准尺寸原页样本。
- `data/manifests/`：来源、许可声明、哈希、下载状态及卷次核验记录。
- `data/catalog.sqlite3`：本地数据库，包含导入页、文本版本、任务、事件和长卷节点。
- `contracts/openapi.json`：当前 API 契约快照。
- `docs/architecture.md`：当前实际架构与维护入口。
- `docs/materials.md`：材料来源、卷次核验与下载状态。
- `docs/evaluation.md`：识别评测与人工审校记录。
- `docs/deployment.md`：线上构建、密钥隔离与单机部署。

## 材料获取

```bash
# 查看已经登记的官方藏本／Commons 材料线索，不下载全部文件
.venv/bin/python scripts/download_sources.py --catalog-only
# 只尝试少量未下载分卷；遇到 403／429 就暂停，不绕过访问限制
.venv/bin/python scripts/download_sources.py --edition mizhi-1681 --limit 1
# 获取来源建议的标准尺寸页面样本（范围限定为卷一前三页）
.venv/bin/python scripts/download_mizhi_samples.py
# 校验 SHA-256、登记原页；不会识别或计为翻译
.venv/bin/python scripts/import_sources.py
```

来源目录并不等于已下载目录。完整卷次与原书内容还需核验，实际状态见 `docs/materials.md`。`verified-volume-notes.json` 记录已发现的来源文件卷次错位。PDF 原文件哈希与来源 SHA-1核对；图像样本记录本地 SHA-256，不能当作原 PDF 的哈希。

## 检查

```bash
.venv/Scripts/python.exe -m pytest -q   # POSIX: .venv/bin/python -m pytest -q
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
npm --prefix apps/web run build:node
.venv/Scripts/python.exe scripts/export_openapi.py
```

可选代码格式工具：`pip install -r backend/requirements-dev.txt`，然后运行 `.venv/bin/ruff format backend scripts`。

测试使用隔离的临时数据库与显式测试替身，不会向供应商发请求，也不会给首页增加假成果。真实 API 效果需配置你自己的密钥后用样本评测。

启动器回归测试位于 `backend/tests/test_dev_launcher.py`，14 项通过，覆盖非法端口、占用端口、跨进程锁、旧 Vinext 服务识别、CMD 编码及含空格路径。实际执行 `.bat --no-open` 已成功复用现有网页，不重复启动后端。

## 识别来源与状态说明

文本处理方式包含视觉 API 识别、智能体转录、PDF 文字提取与人工校勘。识别、初译和发布状态独立记录，不能把机器初稿当成人工校勘。视觉识别默认使用独立配置的 `glm-4.6v-flash`，分阶段保存原文、初译与疑难项。

所有结果为机器草稿，公开发布必须由整理者在工作台显式执行。真实识别评测与卷次映射见 `docs/evaluation.md`。

## 当前边界

单进程 API 内运行一个后台消费者；不要用多个 Uvicorn worker，不适合多台服务器共享 SQLite。现阶段没有 PostgreSQL、向量检索、LangGraph、YOLO、模型微调、付费套餐或公网部署。它们并不是完成当前基础闭环的前提。

SSE 推送实际工具执行事件，最终讲解在完成引用校验后整体显示，尚未做逐 token 输出。未配置密钥时返回明确错误。生产配置与密钥隔离已经接入，Node/Caddy/Docker 单机部署流程见 [部署说明](docs/deployment.md)；容器与公网 HTTPS 验收仍需在部署主机执行。

旧规划、功能任务书和阶段流水报告已从活动文档中清理。可恢复备份位于 `.local-backups/docs-cleanup-20261007.zip`，不含密钥、数据库或原始材料。
