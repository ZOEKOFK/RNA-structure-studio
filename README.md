# RNA 结构预测分析平台

输入 RNA 序列 → 实时预测二级结构 + 最小自由能（MFE）→ 突变前后对比 → 3D 在线查看。

> 对应《RNA结构预测分析平台-项目构建方案》。阶段 0（ViennaRNA 已安装并验证）与阶段 2/3/5 核心闭环已完成。

## 当前状态

- ✅ **阶段 0（部分）**：ViennaRNA 2.7.0 已通过 pip wheel 安装并验证（真实 kcal/mol MFE + 配分函数 bpp）
- ✅ 阶段 2：二级结构服务（`/fold` + `/pairprob`）
- ✅ 阶段 3：突变对比（`/mutate`，ΔMFE + 配对差异）
- ✅ 阶段 4：`/tertiary` 预置 PDB 库命中 + RCSB PDB 序列相似度检索 + RNAComposer 手动桥接
- ✅ 阶段 5：Vue3 + Vite 前端五视图（首页 / 折叠 / 突变 / 深度 / 3D）
- ✅ 阶段 0/3（深度层）：RNA-FM 逐位置嵌入 + 突变嵌入偏移（异步任务 + 缓存；已用真实权重验证 640 维；无 torch 时自动降级为确定性特征嵌入）
- ✅ 自动化测试：`tests/` 覆盖折叠/配对概率/突变/深度任务/缓存淘汰/RCSB 检索/限流（52 项）；前端 `node --test` 覆盖序列规范化（5 项）；GitHub Actions 跑后端测试 + 前端测试 + 构建
- ✅ 单容器部署：Dockerfile 多阶段构建前端并由 FastAPI 静态托管（默认与 `INSTALL_DEEP=1` 两种镜像均已实测）
- ⬜ 待办：RNAComposer 自动提交（官方无公开 REST API，仅能手动粘贴）、forna/Mol* 组件化

## 快速开始

### 后端（FastAPI + ViennaRNA）

```powershell
cd "f:\Bioinformatics Engineering\rna-fold"
pip install -r requirements.txt
# 可选：启用真实能量模型（ViennaRNA Python 绑定）
pip install -r requirements-optional.txt
uvicorn backend.main:app --reload --port 8000
```

验证引擎：http://localhost:8000/api/health （应返回 `engine: "viennarna-python"`）。

> `ViennaRNA` 是可选的：未安装或该平台没有 wheel 时后端自动降级到纯 Python 引擎，服务照常启动。实时折叠限长 1000 nt（超长请求返回 400），配对概率限长 300 nt。

### 可选：启用真实 RNA-FM 深度层

```powershell
# Windows 建议先装 CPU 版 torch（体积更小）
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-deep.txt
```

权重放到 `models/rnafm/`（`pytorch_model.bin` + `config.json`，约 398 MB，已被 `.gitignore` 排除），离线可用；缺失时才会尝试从 `hf-mirror.com` 拉取 `multimolecule/rnafm`。
未安装 torch 时深度视图自动使用确定性特征嵌入，功能可用但非神经网络语义。深度分析限长 300 nt。

首次分析若需加载/下载权重，`/api/health` 的 `deep_note` 会提前提示，任务轮询返回 `stage`（如“正在下载 RNA-FM 权重…”），前端据此展示阶段文案并把超时上限放宽到 10 分钟。

> 已在本机验证：`torch 2.5.1+cpu` + `transformers 4.46.3` + `multimolecule 0.0.8`，`/api/health` 返回 `deep_engine: "rna-fm"`，9 nt 嵌入 640 维；76 nt tRNA 首次约 19 s（含模型加载），缓存命中约 0.5 s。
> 注意不要升级到 transformers ≥ 4.56：它要求 torch ≥ 2.6 才能加载 `.bin` 检查点。

### 前端（Vue3 + Vite + ECharts）

```powershell
cd "f:\Bioinformatics Engineering\rna-fold\frontend"
npm install
npm run dev
```

打开 http://localhost:5173

### 测试

```powershell
pip install -r requirements-dev.txt
python -m pytest tests -q
cd frontend; npm test; npm run build
```

后端测试不加载真实 RNA-FM 权重、不访问网络，约 2–6 秒完成；前端 `npm test` 走 Node 内置测试器，无需额外依赖。

### 单容器部署（Docker）

```powershell
# 默认：ViennaRNA + 前端，深度层使用确定性降级嵌入
docker compose up --build -d

# 启用真实 RNA-FM：构建时安装 CPU 版 torch（镜像约 +200 MB），并挂载本地权重
docker compose build --build-arg INSTALL_DEEP=1
docker compose up -d
```

容器内 FastAPI 同时提供 `/api/*` 与 `frontend/dist` 静态页面，访问 http://localhost:8000 即可。

- **实测**：默认镜像构建约 30 秒（有缓存）、`INSTALL_DEEP=1` 首次构建需额外下载 CPU torch（174.7 MB）；两种镜像均已启动验证 `/api/health`、首页、`/api/fold`、`/api/tertiary`、`/api/deep/*`。
- **非 root**：镜像内以 `app`（uid 10001）运行；`HEALTHCHECK` 内置在镜像里，`docker run` 单独部署也有健康状态。
- **挂载**：`./data` 只读（示例库）、`./models` 只读、嵌入缓存写入命名卷 `rna-cache`（`/app/cache`），避免 bind mount 的宿主机属主问题。
- **优雅关闭**：`stop_grace_period: 30s`，关闭时自动取消未完成的深度任务。
- **单进程约束**：任务表、限流窗口、RCSB 缓存都在进程内存，容器固定 `--workers 1`；设置 `WEB_CONCURRENCY` / `UVICORN_WORKERS` > 1 会在启动阶段直接报错。
- **镜像追溯**：`GIT_COMMIT=$(git rev-parse --short HEAD) docker compose build` 会把 commit 写进 OCI `LABEL`。

> 示例库改动**无需重启**：后端按 `examples.json` 的 mtime 自动重载；但 `./data` 只读挂载意味着只能编辑宿主机文件，不能通过接口写入。

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RNA_CORS_ORIGINS` | `*` | 允许的前端来源，逗号分隔；生产建议收紧 |
| `RNA_LOG_LEVEL` | `INFO` | 后端日志级别 |
| `RNA_MAX_SEQ_LEN` | `1000` | 实时折叠限长 |
| `RNA_BPP_MAX_LEN` | `300` | 真实配对概率矩阵限长 |
| `RNA_BOLTZMANN_MAX_LEN` | `200` | 降级引擎配对概率限长 |
| `RNA_FALLBACK_MAX_LEN` | `300` | 纯 Python 折叠降级限长 |
| `RNA_MAX_DEEP_LEN` | `300` | 深度分析限长 |
| `RNA_CACHE_MAX_FILES` | `500` | 嵌入缓存文件上限（按最近读取时间 LRU 淘汰） |
| `RNA_TERTIARY_RATE_LIMIT` | `30` | `/api/tertiary` 每 IP 每分钟请求上限；`0` 关闭 |
| `RNA_TRUST_PROXY` | `0` | 设为 `1` 时从 `X-Forwarded-For` 取真实客户端 IP；仅在 Nginx/Caddy/负载均衡之后启用，直接暴露公网必须保持 `0` |
| `RNA_CACHE_DIR` | `data/cache/embeddings` | 嵌入缓存目录（Docker 内为 `/app/cache`） |
| `WEB_CONCURRENCY` / `UVICORN_WORKERS` | 未设置 | 必须 ≤1：任务表/限流/检索缓存为进程内状态，多进程会在启动时报错 |

## 折叠引擎三级降级

| 级别 | 引擎 | 何时生效 | 能量 | 配对概率 |
|---|---|---|---|---|
| 1 | ViennaRNA Python 绑定（`import RNA`） | pip 安装 wheel | 真实 kcal/mol | 配分函数 bpp（真实系综概率） |
| 2 | ViennaRNA CLI（`RNAfold`） | PATH 中可检测到 CLI | 真实 kcal/mol | 降级采样 |
| 3 | 纯 Python Nussinov + Boltzmann 采样 | 无 ViennaRNA | 估计值（非 kcal/mol） | 采样估计 |

当前环境走第 1 级。pip 的 Windows wheel 只含 Python 绑定（无 `RNAfold.exe`），因此主引擎直接用 Python API；CLI 与纯 Python 作为兜底。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 引擎状态、ViennaRNA 版本、各层限长与缓存上限 |
| GET | `/api/examples` | 预置 RNA 示例库（9 个） |
| POST | `/api/fold` | `{sequence}` → dot-bracket + MFE + 碱基对（支持 FASTA / DNA 的 T 自动转 U） |
| POST | `/api/pairprob` | `{sequence}` → 配对概率矩阵（≤300 nt） |
| POST | `/api/mutate` | `{sequence, position, base}` → ΔMFE + 配对差异 |
| POST | `/api/tertiary` | `{sequence}` → 预置 PDB 命中 / RCSB 相似结构 / RNAComposer 输入 |
| POST | `/api/deep/embed` | `{sequence}` → `{job_id}`（逐位置嵌入，异步，≤300 nt） |
| POST | `/api/deep/mutate` | `{sequence, position, base}` → `{job_id}`（嵌入偏移，异步） |
| GET | `/api/deep/jobs/{job_id}` | 任务状态 / 进度 / 排队位置 / 结果（`queued → running → done / error / cancelled`） |
| DELETE | `/api/deep/jobs/{job_id}` | 取消排队中或运行中的深度任务（已完成返回 409）；运行中的任务在下一个计算检查点中断 |

> 序列统一支持 FASTA（忽略 `>` / `;` 描述行）；含 IUPAC 简并碱基（N/R/Y…）或非法字符时返回 400 并提示原因。

## 前端功能

- 首页：序列输入（FASTA 感知、自动 U 化、简并碱基与限长校验）、示例库一键「折叠 / 突变 / 深度 / 3D」
- 折叠视图：MFE 卡片、结构统计、自绘弧线图（GC/AU/GU 配色 + 图例）、ECharts 配对概率热力图、复制 dot-bracket、下载 JSON 结果、示例快速切换
- 突变对比：位点滑杆、参考碱基提示、ΔMFE 稳定性趋势（↑/↓）、野生型/突变型双结构图、断裂/新增碱基对
- 深度分析：逐位置嵌入（PCA 轨迹 + 嵌入强度曲线）、突变嵌入偏移条形图（标注突变位）、WT/MT 嵌入空间对比；异步轮询进度条、排队位置提示、任务取消、结果导出 JSON
- 3D 视图：命中预置 PDB → 直接嵌入 Mol* Viewer；未命中 → RCSB PDB 按序列相似度（≥90%）列出已解析实验结构，点击任意一条切换 Mol* 视图；同时生成 RNAComposer 输入（任务名 / 序列 / dot-bracket），可一键复制并跳转官网手动预测
- 全局提示：后端不可达或 5xx 时右下角弹出错误提示（4xx 由各页面就地展示原因）

## 预置示例库

| 名称 | 类型 | 长度 | MFE (kcal/mol) | PDB |
|---|---|---|---|---|
| 测试发夹 | 合成 | 9 | −1.2 | — |
| tRNA^Phe（酵母） | tRNA | 76 | −22.4 | 4TNA |
| 5S rRNA（大肠杆菌） | rRNA | 120 | −50.9 | — |
| 锤头核酶 | 核酶 | 34 | −8.1 | 1HMH |
| HDV 核酶 | 核酶 | 72 | −33.6 | 1DRZ |
| TPP 核糖开关 | 核糖开关 | 80 | −21.6 | 2GDI |
| UUCG 四环发夹 | 合成 | 12 | −5.5 | — |
| 长茎发夹 | 合成 | 20 | −18.6 | — |
| 双发夹 | 合成多分支 | 21 | −5.8 | — |

> 真实分子的序列取自 RCSB PDB 官方 FASTA（1HMH / 1DRZ / 2GDI），结构与 MFE 由 ViennaRNA 预测。

## 目录结构

```text
rna-fold/
├── backend/
│   ├── main.py            # FastAPI 接口
│   ├── rna_fold.py        # 折叠引擎（绑定 / CLI / 纯 Python 三级）
│   ├── rna_fm.py          # RNA-FM 深度层（真实模型 / 降级嵌入、异步任务、缓存）
│   ├── rcsb.py            # RCSB PDB 序列相似度检索（公开 Search API，无需 key）
│   └── composer_api.py    # RNAComposer 手动桥接（生成可粘贴的输入文本）
├── data/
│   ├── examples/examples.json
│   └── cache/embeddings/  # 按序列哈希缓存的嵌入向量（LRU 上限 500）
├── models/rnafm/          # 可选：RNA-FM 本地权重
├── frontend/              # Vue3 + Vite + ECharts（test/ 为 Node 内置测试）
├── tests/                 # pytest 自动化测试
├── .github/workflows/ci.yml  # CI：后端测试 + 前端测试 + 构建
├── Dockerfile             # 多阶段：构建前端 + pip 安装 ViennaRNA wheel 运行时（非 root）
├── .dockerignore
├── docker-compose.yml     # 单容器（API + 静态页面），可选挂载权重
├── requirements.txt       # 基础依赖（必装）
├── requirements-optional.txt  # ViennaRNA 绑定（可选，真实能量模型）
├── requirements-deep.txt  # torch + transformers（可选，真实 RNA-FM）
└── requirements-dev.txt   # pytest / httpx（测试用）
```

## 后续路线

- 阶段 4：RNAComposer 自动提交（官方**无公开 REST API、无 API key**，交互模式匿名可用 ≤500 nt，当前以“生成输入 → 官网手动预测”方式覆盖）
- 阶段 5：forna 交互圈图（可选，替换自绘弧线图）、Mol* 由 iframe 改为嵌入式组件
- 可选增强：RCSB 检索结果缓存与自定义相似度阈值（当前固定 ≥90%、内存 LRU 100 条）