# RNA 结构预测分析平台 · 技术文档

> 版本：v0.1.0 ｜ 代码提交：`4468898` ｜ 文档提交：`2085279` ｜ 最后更新：2026-11-13
> 本文面向三类读者：**评审**（想知道系统到底怎么工作、哪些结论可信）、**接手者**（要改代码）、**复现者**（要跑起来并验证）。
> 与 `README.md` 的分工：README 是"怎么用"，本文是"为什么这么设计、怎么实现、边界在哪"。
> 线上实例：<https://rna-structure-studio.app.workbuddy.host/?sharecode=H6MR5HLIVE>（部署形态见 §9.3）

---

## 1. 系统概览

一句话：**输入一条 RNA 序列，同步返回二级结构 / 最小自由能 / 配对概率，异步返回深度嵌入与三维坐标；所有计算在本地完成；外部依赖只有两项，且都不阻塞主流程：首次加载 RNA-FM 时的权重下载（也可预先放本地）、以及 3D 页的 RCSB 检索。**

### 1.1 分层结构

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 浏览器  Vue 3 + Vite（单页应用，5 个页面 + 404）                      │
│  Home ── Fold ── Mutate ── Deep ── Tertiary                          │
│  可视化：forna(d3) 圈图 · 自绘 SVG 弧线图 · ECharts 热力图/PCA · Mol* │
│  路由懒加载：Fold/Deep/Mutate/Tertiary 均动态 import，首屏只含首页     │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ 同源 HTTP/JSON（开发期由 Vite 代理 /api）
┌─────────────────────────────▼────────────────────────────────────────┐
│ 应用层  FastAPI（backend/main.py，531 行）                            │
│  中间件：CORS → GZip(minimum_size=1024)                               │
│  同步接口：/api/health /examples /fold /pairprob /mutate /tertiary     │
│            /api/pdb/{id} /api/3d/models/{id}                          │
│  异步接口：/api/deep/* /api/3d/*（提交→job_id→轮询→取结果/取消）        │
│  静态托管：frontend/dist（SPA fallback + 路径穿越防护）                │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ 纯函数调用（同进程，无 RPC）
┌─────────────────────────────▼────────────────────────────────────────┐
│ 引擎层                                                                │
│  rna_fold.py  二级结构：ViennaRNA 绑定 → RNAfold CLI → Nussinov+Boltzmann│
│  rna_fm.py    深度层：真实 RNA-FM(640d) → 确定性特征嵌入(34d)          │
│  rna3d.py     三维：外部引擎钩子 → 内置约束松弛建模                    │
│  rcsb.py      RCSB Search API（序列相似度 ≥90%，LRU 100）              │
│  composer_api.py  RNAComposer 手动桥接（只生成输入，不提交）           │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────────┐
│ 数据层（全部本地文件，无数据库）                                       │
│  data/examples/examples.json  9 条预置分子 + Rfam 注释                │
│  data/pdb/*.pdb               5 个实验结构坐标（4TNA/1EHZ/1HMH/1DRZ/2GDI）│
│  data/cache/embeddings/*.json 嵌入缓存（LRU 500）                     │
│  data/cache/models3d/*.pdb    3D 模型缓存（内容哈希，带算法版本）      │
│  models/rnafm/                可选：RNA-FM 权重 398 MB（gitignore）    │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 组件清单

| 文件 | 职责 | 行数 | 关键点 |
|---|---|---|---|
| `backend/main.py` | 路由、校验、任务编排、静态托管 | 531 | 中间件顺序、限流、单进程断言、路径穿越防护 |
| `backend/rna_fold.py` | 二级结构三级引擎 | 351 | 绑定/CLI/纯 Python 三档降级，配对概率两条路径 |
| `backend/rna_fm.py` | 深度嵌入、突变偏移、任务队列 | 645 | 惰性加载、手工剥离权重前缀、幂迭代 PCA、协作式取消 |
| `backend/rna3d.py` | 本地 3D 建模 | 529 | 四类括号解析、约束松弛、骨架螺旋铺原子、版本化缓存 |
| `backend/rcsb.py` | 实验结构检索 | 144 | 重试策略、204 语义、LRU |
| `backend/composer_api.py` | RNAComposer 输入生成 | 21 | 刻意不做自动提交 |
| `frontend/src/` | 5 页面 + 4 组件 | 3702 | 懒加载、forna 样式作用域隔离、Mol* 动态导入 |
| `tests/` | 后端测试 | 908 | 72 项，不联网、不加载真实权重 |

代码规模：后端 2221 行 / 前端 3702 行 / 测试 908 行；仓库跟踪 57 个文件、约 2.9 MB（含 PDB 坐标与 LICENSE）。

---

## 2. 关键设计决策

这一节是全文档最重要的部分：每个决策都写清 **问题 → 选择 → 代价**，以及代码里的落点。

### D1 二级结构做三级降级，而不是"必须装 ViennaRNA"

- **问题**：ViennaRNA 在部分平台（如某些 macOS、无 wheel 的 Python 版本）装不上，但项目必须"克隆下来就能跑"。
- **选择**：`rna_fold.fold()` 依次尝试 ① `import RNA` Python 绑定 → ② PATH 中的 `RNAfold` CLI → ③ 纯 Python Nussinov + Boltzmann 采样。每级失败自动下沉，响应里用 `engine` 字段声明实际使用的引擎。
- **代价**：降级引擎给出的是**无量纲估计值**，不是 kcal/mol。因此前端在 MFE 卡片上把单位替换成"估计值"，`/api/mutate` 返回 `note` 提示"ΔMFE 由降级引擎估计"。
- **代码**：`rna_fold.py:300-330`（`fold`）、`:282-294`（`_fold_fallback`）、`:89-95`（`engine_status`）。

### D2 实时层与深度层分离：同步 vs 异步

- **问题**：RNA-FM 在 CPU 上首次推理要 8 秒以上（含 398 MB 权重加载），做不到"实时"；但把它砍掉就失去差异化。
- **选择**：折叠/配对概率/突变走**同步**接口（毫秒级）；嵌入与 3D 建模走**异步任务**（提交返回 `job_id`，前端 500 ms 轮询进度，支持取消与排队位置）。
- **代价**：需要任务表、进度上报、取消语义、超时兜底；且任务表在进程内存，天然不能多进程（见 D4）。
- **代码**：`main.py:385-438`（深度任务路由）、`rna_fm.py:573-644`（提交/取消/查询）、`rna3d.py:484-521`。

### D3 三维结构走"实验结构优先"，不做 RNAComposer 自动提交

- **问题**：RoseTTAFold NA 需要 Linux + CUDA，本机跑不动；RNAComposer 官方**没有公开 REST API，也没有 API key**，任何"自动调用"都只能靠模拟浏览器抓取，脆弱且违反使用预期。
- **选择**：四级策略 —— ① 命中预置库直接展示本地 PDB（离线可用）→ ② RCSB 序列相似度检索（≥90%）→ ③ 内置几何建模器出拓扑级模型 → ④ 生成 RNAComposer 可粘贴文本，由用户手动提交。
- **代价**：第 ③ 级精度有限（见 §7.4 实测 Rg 偏差 +29%），必须在 UI 上明确标注，否则就是误导。
- **代码**：`main.py:304-382`、`rna3d.py:311-316`、`composer_api.py:16-21`。

### D4 强制单进程，而不是支持横向扩展

- **问题**：任务表、限流窗口、RCSB 缓存都是进程内状态。多 worker 下轮询会打到没有该任务的进程（404），限流也会按进程重复计数。
- **选择**：启动时检测 `WEB_CONCURRENCY` / `UVICORN_WORKERS`，大于 1 直接抛错拒绝启动（`main.py:32-43`），把问题暴露在启动阶段而不是运行中。容器固定 `--workers 1`。
- **代价**：无法横向扩展，重启丢任务。对"单机教学/演示工具"是合理取舍；要扩展必须把任务表外置（见 §11）。

### D5 缓存按内容哈希 + LRU + 算法版本号

- **问题**：嵌入计算贵（8 s 冷启动）、3D 建模也不便宜，重复请求必须命中；但算法改动后旧缓存不能继续返回。
- **选择**：嵌入缓存键 `sha1("engine|MAX_DEEP_LEN|seq")`，读取时 `os.utime` 刷新 mtime，超 500 个文件按 mtime 淘汰最旧；3D 缓存键 `sha1("engine:v{BUILTIN_VERSION}\0seq\0structure")`，**改几何算法就把版本号 +1**，旧缓存自动失效（`rna3d.py:52`）。
- **代价**：缓存写入时把向量量化到 3 位小数（`rna_fm.py:366`），导致首次计算与缓存命中的 PCA 结果存在 1e-3 级差异（见评估报告 §5.6）。

---

## 3. 模块详解

### 3.1 序列处理：一处定义，全链路复用

```python
normalize_sequence(raw)   # 去 FASTA 头（> / ;）→ 去空白 → 大写 → T→U
validate_sequence(seq)    # 空 / IUPAC 简并碱基 / 非法字符 → ValueError
```

- FASTA 判定是"**任意一行以 `>` 开头**"，而不是只看第一行，避免用户粘贴带前导空行时解析失败。
- 简并碱基（`NRYSWKMBDHV`）**显式拒绝**而不是猜测展开：猜错会给出看似合理但错误的能量。
- 前端 `src/sequence.js` 用同一套规则做实时校验（5 项单元测试覆盖），保证"前端放行 = 后端可算"。

### 3.2 二级结构引擎（rna_fold.py）

| 级别 | 实现 | 能量语义 | 配对概率 | 限长 |
|---|---|---|---|---|
| 1 | `RNA.fold()` / `RNA.fold_compound().pf().bpp()` | 真实 kcal/mol（Turner 模型） | 配分函数 bpp（真实系综） | 折叠 1000 nt / bpp 300 nt |
| 2 | `subprocess RNAfold --noPS`，30 s 超时 | 真实 kcal/mol | 无（降级采样） | 同上 |
| 3 | Nussinov DP + Boltzmann 采样（150 次） | 无量纲估计 | 采样频率估计 | 折叠 300 nt / 概率 200 nt |

- **Nussinov**（`_nussinov`）：标准区间 DP，`g[i][j] = max(g[i][k]+g[k+1][j], score(i,j)+g[i+1][j-1])`，碱基对打分 `GC=3 / AU=2 / GU=1`，强制最小发夹环 3 nt。复杂度 O(n³) 时间、O(n²) 空间。
- **Boltzmann 采样**（`_pairing_probability`）：先算配分函数 `Q[i][j]`（同样的 O(n³) 递推，权重 `exp(score)`），再按条件概率递归采样 150 次，统计每个位点对的出现频率。固定 `seed=0`，所以结果可复现。
- **bpp 的真实来源**：`RNA.fold_compound(seq).pf()` 后取 `bpp()`（1-based），只保留 ≥0.005 的格子并四舍五入到 4 位，避免 JSON 里塞满 `1e-9` 噪声。

### 3.3 深度层（rna_fm.py）

**两条引擎路径**

| 路径 | 触发条件 | 维度 | 说明 |
|---|---|---|---|
| 真实 RNA-FM | `torch` + `multimolecule` 可用，且权重可加载 | 640 | 权重优先本地 `models/rnafm/`，否则 `hf-mirror.com` 的 `multimolecule/rnafm` |
| 确定性特征嵌入 | 上者任一失败 | 34 | 纯 Python，无依赖，见下表 |

降级嵌入的 34 维构成（`_fallback_vectors`）：当前碱基 one-hot(4) + 前一位(4) + 后一位(4) + 4 组正余弦位置编码(8) + 三核苷酸哈希桶(8) + 是否配对(1) + 配对距离(1) + 配对类型 GC/AU/GU(3) + 嵌套深度(1)。**它不是"假模型"**：至少编码了碱基、邻域与二级结构上下文，因此在没有 torch 的机器上，页面上的趋势仍有意义，只是不能用它做生物学结论。

**权重加载的两个坑（已修）**

1. RNA-FM 检查点是"骨干 + 预测头"格式，键名带 `model.` 前缀，与 `RnaFmModel` 参数名不一致，直接 `from_pretrained` 会**静默全部随机初始化**。所以 `_load_backbone_weights` 手工剥离前缀，并检查 `unexpected_keys` 非空即报错。
2. `multimolecule==0.0.8` 的 tokenizer 传参方式与新版 transformers 不兼容，报 `Tokenizer class RnaTokenizer does not exist` 后静默降级。因此 `requirements-deep.txt` 锁死 `transformers==4.46.3` + `multimolecule==0.0.8` + `torch 2.5.1+cpu`，并在 README 里写明"不要放开版本锁"。

**PCA 不引 numpy**：`_pca2` 用幂迭代（40 次）求第一主成分，对中心化数据做 deflation 后再求第二主成分，最后按最大绝对值归一化。目的是零依赖、结果确定；代价是纯 Python 循环，实测 40 nt × 640 维需要 **282 ms**，300 nt 缓存命中仍需 **1.9 s**（见 §7.3）。这是当前深度层的主要性能瓶颈。

**任务队列**：`ThreadPoolExecutor(max_workers=1)` + 内存字典 `_JOBS`（上限 50，先进先出淘汰）+ 每任务一个 `threading.Event`。取消是**协作式**的：在计算阶段边界调用 `_raise_if_cancelled()`；torch 前向不可中断，所以前向返回后再检查一次。`get_job` 在 `queued` 时会现场计算排队位置。

### 3.4 本地 3D 建模（rna3d.py）

输入「序列 + dot-bracket」，输出 PDB 文本，四步：

1. **解析配对**（`parse_pairs`）：`() [] {} <>` 四类括号各自独立栈，因此能表达伪结；伪结判定用 `i < k < j < l` 的交叉条件（`[[..]]` 这种嵌套**不算**伪结）。检测到伪结时 `pseudoknot_note()` 返回提示文案，接口透传给前端。
2. **环形初值**（`_initial_layout`）：按骨架周长 `5.9 Å/nt` 反推半径，但**上限 40 Å**。不设上限时 76 nt 的 tRNA 会摊成半径 71 Å 的大圆盘（Rg≈53 Å），Mol* 里像一只扁平蜘蛛；压到 40 Å 后，再让嵌套越深的位置越向内收（−2 Å/层）并抬高（+6 Å/层），结构才有三维层次。
3. **约束松弛**（`_relax`）：240 轮 Gauss-Seidel。每轮先把所有碱基对拉到 10.4 Å，再把相邻残基拉到 5.9 Å，最后用 0.005 的权重把点往初值回拉一点点防止漂移。回拉权重必须很小，否则环形初值会把已经闭合的碱基对重新拉开。
4. **铺骨架原子**（`_backbone_points`）：每个残基输出 `P / O5' / C5' / C4' / C3' / O3'` 六个原子。单段要容纳 9.1 Å 弧长却只有 5.9 Å 弦长，若走半圆弧主链会在弦两侧各摆 ~3 Å，Mol* 的卡通带会剧烈抖动；因此改成**绕弦的小半径螺旋**（半径约 1.1 Å），轴向位移随弧长线性增长、周向转一整圈，两端半径收敛到 0，保证段首尾精确落在 P(i)、P(i+1) 上。

输出带 `REMARK SIMPLIFIED RNA MODEL` 标记，Mol* 因此能识别为核酸链并绘制骨架带状表示。

### 3.5 RCSB 检索（rcsb.py）

- 用公开 Search API（`sequence` service，`identity_cutoff=0.9`），返回 entry 级结果，再并发（≤4 线程）拉条目摘要（分辨率、方法、标题）。
- **204 不是错误**：RCSB 零命中时返回 204 + 空响应体，代码把它解析成 `{}` 而不是抛异常。
- 重试策略：仅对 5xx / 429 / 网络错误重试 2 次，退避 0.4 s、0.8 s；4xx 直接失败。
- LRU 100 条，键含 `(sequence, top, identity_cutoff)`；**失败结果不写缓存**，避免一次网络抖动把后续查询永久钉死。

### 3.6 应用层细节（main.py）

- **中间件顺序**：先加 CORS 再加 GZip。300 nt 配对概率矩阵 JSON 约 442 KB，gzip 后 3.8 KB（实测压缩到 0.9%）。
- **限流**：`/api/tertiary` 会向外发请求，按客户端 IP 做 60 s 滑动窗口（默认 30 次/分），超限返回 429 + `Retry-After`。`RNA_TRUST_PROXY` 默认 **0**：直连公网时 `X-Forwarded-For` 可伪造，开启会让限流形同虚设。
- **静态托管**：仅当 `frontend/dist` 存在时挂载，且注册在**所有 `/api` 路由之后**；`../` 越界用 `realpath` + 前缀校验拦住；`api/` 开头的未知路径返回 404 而不是 index.html，避免前端把接口 404 当成页面。
- **优雅关闭**：lifespan 退出时调用 `rna_fm.cancel_all()` / `rna3d.cancel_all()`，容器 `stop_grace_period: 30s` 给长任务留出退出时间。

---

## 4. 接口契约

### 4.1 端点总表

| 方法 | 路径 | 同步/异步 | 限长 | 说明 |
|---|---|---|---|---|
| GET | `/api/health` | 同步 | — | 引擎、版本、各层限长、缓存上限 |
| GET | `/api/examples` | 同步 | — | 9 条示例 + Rfam 注释（按 mtime 缓存） |
| POST | `/api/fold` | 同步 | 1000 nt | dot-bracket + MFE + 配对列表 |
| POST | `/api/pairprob` | 同步 | 300 nt（真实）/ 200 nt（降级） | 配对概率矩阵，gzip |
| POST | `/api/mutate` | 同步 | 1000 nt | 双折叠 → ΔMFE + 断裂/新增对 |
| POST | `/api/tertiary` | 同步 | 500 nt（超过则不再检索） | 预置库 / RCSB / RNAComposer 桥接 |
| GET | `/api/pdb/{id}` | 同步 | — | 本地 PDB 坐标，未收录 404 |
| POST | `/api/3d/predict` | 异步 | 500 nt | 返回 `job_id` + 实际使用的结构 + 伪结提示 |
| GET | `/api/3d/jobs/{id}` | 同步 | — | 状态 / 阶段 / 结果（引擎、缓存、耗时、原子数） |
| DELETE | `/api/3d/jobs/{id}` | 同步 | — | 取消；已结束返回 409 |
| GET | `/api/3d/models/{id}` | 同步 | — | 下载缓存的 PDB（id 为 40 位 sha1） |
| POST | `/api/deep/embed` | 异步 | 300 nt | 逐位置嵌入 + PCA + L2 |
| POST | `/api/deep/mutate` | 异步 | 300 nt | 逐位置嵌入偏移 Top5 + 双投影 |
| GET | `/api/deep/jobs/{id}` | 同步 | — | 状态 / 进度 / 排队位置 / 结果 |
| DELETE | `/api/deep/jobs/{id}` | 同步 | — | 取消；已结束返回 409 |

### 4.2 `/api/health` 字段（实测）

```json
{
  "status": "ok",
  "engine": "viennarna-python",
  "rnafold_available": true,
  "viennarna_version": "2.7.0",
  "max_sequence_len": 1000,
  "pairprob_max_len": 300,
  "deep_engine": "rna-fm",
  "deep_model": "RNA-FM (multimolecule/rnafm)",
  "deep_max_len": 300,
  "deep_ready": false,
  "deep_error": null,
  "deep_cache_max": 500,
  "deep_note": "首次深度分析将加载本地 RNA-FM 权重（约 398 MB）",
  "engine_3d": "builtin",
  "model_3d_max_len": 500,
  "model_3d_note": "内置简化建模（教学用粗粒度模型，精度低于 RNAComposer / RhoFold+）"
}
```

注意 `deep_ready` 与 `deep_engine` 的语义差别：前者表示**权重是否已加载进内存**（惰性加载，首请求前一直是 `false`），后者表示**依赖是否具备**。前端据此显示"引擎可用 / 首次较慢"。

### 4.3 错误码矩阵（实测）

| 输入 | 状态码 | detail |
|---|---|---|
| 含简并碱基 `N` | 400 | 含简并碱基 N，无法预测结构；请展开为 A/U/C/G |
| 空序列 | 400 | 序列为空：请粘贴 A/U/C/G 序列或 FASTA 记录 |
| 1001 nt | 400 | 序列长度 1001 nt 超过实时折叠限长 1000 nt |
| 突变位点越界 | 400 | 突变位点超出序列范围 |
| 非法碱基 | 400 | 碱基必须是 A/U/C/G |
| dot-bracket 长度不符 / 含非法字符 | 400 | 明确说明期望 |
| 非法 PDB ID（非 4 位字母数字） | 400 | — |
| 未收录 PDB / 未知 job / 非法 model id | 404 | — |
| 取消已结束任务 | 409 | 任务已处于 done 状态，无法取消 |
| `/api/tertiary` 超限流 | 429 + `Retry-After` | 请求过于频繁 |
| 未知非 api 路径 | 200（SPA fallback） | 返回 index.html |
| 未知 `api/` 路径 | 404 | 接口不存在 |

---

## 5. 典型数据流

### 5.1 折叠（同步，一次往返）

```text
用户点「折叠」
  → GET /?seq=…（路由带 query）
  → Fold.vue onMounted：并行请求 /api/fold 与 /api/pairprob（长度 ≤ bpp 限长时）
  → Promise.all 返回后 nextTick() 再画热力图（DOM 未就绪时 echarts.init 会拿到 null）
  → forna 圈图 / 弧线图二选一渲染；>400 nt 强制弧线图
```

### 5.2 突变 + 自动深度（同步 + 异步接力）

```text
POST /api/mutate
  ├─ fold(wt) + fold(mt)                       ← 两次同步折叠
  ├─ 集合差：broken = wt_pairs − mt_pairs
  │           formed = mt_pairs − wt_pairs
  └─ 返回 ΔMFE / 结构 / 差异对
前端拿到结果后立刻 POST /api/deep/mutate（不阻塞结构对比的渲染）
  → 后台单 worker：wt 嵌入（缓存命中约 0.3–0.7 s，未命中约 8 s）
                  → mt 嵌入（同理）
                  → 逐位 L2 偏移 + 双投影
  → 前端 500 ms 轮询 /api/deep/jobs/{id}，实时显示进度条
```

### 5.3 3D 任务（提交 → 轮询 → 取文件）

```text
POST /api/3d/predict {sequence, structure?}
  ├─ 结构缺省时先 fold 拿 dot-bracket
  ├─ 校验（长度一致、字符合法）→ submit → job_id + pseudoknot_note
  └─ 后台：外部引擎（若配置 RNA_3D_CMD）→ 失败回退内置建模 → 写缓存
前端轮询 /api/3d/jobs/{id} → done 后把 result.url 交给 Mol*
  → GET /api/3d/models/{sha1} → FileResponse(chemical/x-pdb)
```

---

## 6. 配置项分组

| 组 | 变量 | 默认 | 说明 |
|---|---|---|---|
| 限长 | `RNA_MAX_SEQ_LEN` / `RNA_BPP_MAX_LEN` / `RNA_BOLTZMANN_MAX_LEN` / `RNA_FALLBACK_MAX_LEN` / `RNA_MAX_DEEP_LEN` / `RNA_MAX_3D_LEN` | 1000 / 300 / 200 / 300 / 300 / 500 | 所有限长都可调，但调大前先看 §7 的复杂度曲线 |
| 缓存 | `RNA_CACHE_DIR` / `RNA_3D_CACHE_DIR` / `RNA_CACHE_MAX_FILES` | `data/cache` / `$RNA_CACHE_DIR/models3d` / 500 | 容器内必须指向可写卷 |
| 外部服务 | `HF_ENDPOINT` / `RNA_3D_CMD` / `RNA_3D_TIMEOUT` | `hf-mirror.com` / 未设置 / 600 s | `RNA_3D_CMD` 占位符：`{seq_file}` `{ss_file}` `{out_pdb}` `{workdir}` |
| 安全/网络 | `RNA_CORS_ORIGINS` / `RNA_TERTIARY_RATE_LIMIT` / `RNA_TRUST_PROXY` | `*` / 30 / `0` | 生产必须收紧前两者、代理后才开第三个 |
| 运行 | `RNA_LOG_LEVEL` / `WEB_CONCURRENCY` | `INFO` / 未设置 | 后者 >1 会直接拒绝启动 |

---

## 7. 性能特征（本机实测）

环境：AMD Ryzen 7 7735H（8C/16T）、15.2 GB RAM、Windows 11、Python 3.9.13、ViennaRNA 2.7.0、`torch 2.5.1+cpu`。完整方法与原始输出见 `EVALUATION.md`。

### 7.1 折叠延迟随长度增长

| 长度 | 中位数 | 最小 / 最大 | 碱基对 |
|---|---|---|---|
| 50 nt | 0.61 ms | 0.59 / 0.96 | 10 |
| 100 nt | 3.10 ms | 3.04 / 3.36 | 19 |
| 200 nt | 14.51 ms | 14.36 / 15.33 | 45 |
| 300 nt | 35.52 ms | 34.94 / 37.58 | 57 |
| 500 nt | 104.76 ms | 103.87 / 105.52 | 118 |
| 1000 nt | 525.68 ms | 522.99 / 558.75 | 267 |

100 → 1000 nt 增长约 170 倍，介于 O(n²) 与 O(n³) 之间（ViennaRNA 的剪枝让实际常数低于理论最坏情况）。**1000 nt 约 0.5 s** 是"实时"体验的上限依据：更长的序列要换异步。

### 7.2 配对概率 / 突变

| 项 | 耗时 | 备注 |
|---|---|---|
| pairprob 100 nt | 7.81 ms | JSON 49.6 KB → gzip 0.9 KB |
| pairprob 200 nt | 40.77 ms | 196.5 KB → 1.6 KB |
| pairprob 300 nt | 112.07 ms | 442.1 KB → 3.8 KB（压缩至 0.9%） |
| mutate 76 nt（双折叠 + 差异） | 5.69 ms | 主要成本是两次 fold |

### 7.3 深度层

| 场景 | 耗时 | 说明 |
|---|---|---|
| 权重加载（本地 398 MB） | 8.2 s | 磁盘热缓存；首次下载另算 |
| embed 120 nt（未缓存） | 0.97 s | 含推理 |
| embed 300 nt（未缓存） | 2.39 s | — |
| embed 76 nt（缓存命中，HTTP 端到端） | 538 ms | 其中 PCA 实占 434 ms，缓存读取仅 7 ms |
| embed 300 nt（缓存命中） | 1.89 s | **PCA 是瓶颈**，非推理 |
| `_pca2` 单独（40 nt × 640 维） | 282 ms | 纯 Python 40 次幂迭代 × 2 成分 |
| deep/mutate 76 nt（一侧未缓存） | 12.2 s | 两次推理 + 两次 PCA |

结论：**缓存解决的是推理，没解决 PCA**。把 `_pca2` 换成 numpy（`X @ X.T` 的特征分解）预计可把 300 nt 的 1.9 s 降到毫秒级，是性价比最高的优化项。

### 7.4 3D 建模

| 长度 | 首次耗时 | 原子数 | 碱基对平均距离 | 骨架平均 | 回旋半径 Rg |
|---|---|---|---|---|---|
| 76 nt | 188.6 ms | 456 | 10.54 Å（max 10.89） | 5.92 Å | 29.9 Å |
| 120 nt | 57.8 ms | 720 | 10.51 Å（max 11.07） | 5.91 Å | 35.7 Å |
| 300 nt | 115.4 ms | 1800 | 10.55 Å（max 10.97） | 5.88 Å | 26.5 Å |
| 500 nt | 196.9 ms | 3000 | 10.45 Å（max 10.88） | 5.88 Å | 28.9 Å |

（76 nt 首次偏慢是因为包含模块首次调用与 PDB 写盘；缓存命中为 0 耗时。）

**这些指标证明的是求解器收敛（约束被满足），不是物理精度**。与实验结构对比：tRNA 4TNA 的 P 原子回旋半径实测 23.2 Å，模型 29.9 Å，偏大约 29%。因此它是拓扑预览级模型——这一点在接口 `model_3d_note`、页面提示与 README 三处一致声明。

### 7.5 前端体积

| 资源 | 原始 | gzip | 何时加载 |
|---|---|---|---|
| 首屏（index + vue + index.css） | 145 KB | 52 KB | 立即 |
| ECharts chunk | 566 KB | 188 KB | 折叠/突变/深度页 |
| Mol*（app chunk） | 5.24 MB | 1.49 MB | 仅 Tertiary 页动态导入 |
| Mol* 浅色主题 CSS | 71 KB | 15 KB | 同上 |

首屏 52 KB（gzip）是懒加载设计的直接收益；Mol* 的 5.2 MB 不进入首屏。

---

## 8. 可靠性与边界

### 8.1 降级矩阵

| 组件 | 正常 | 一级降级 | 二级降级 | 用户可见信号 |
|---|---|---|---|---|
| 二级结构 | ViennaRNA 绑定 | RNAfold CLI | Nussinov+Boltzmann | MFE 单位变「估计值」、引擎卡显示「纯 Python 降级」 |
| 深度层 | RNA-FM 640d | 34d 确定性特征 | — | 页面标签「降级嵌入」+ 可展开原因与启用方法 |
| 3D | 外部引擎（若配置） | 内置几何建模 | — | 结果里 `engine` 字段与 `fallback` 文案 |
| 3D 检索 | RCSB 命中 | 未命中→RNAComposer 桥接 | 网络失败→桥接 + 错误提示 | 结果卡片文案区分三种情况 |
| 圈图 | forna 渲染 | 自动切弧线图 | — | 提示「已自动切换到自绘弧线图」 |

### 8.2 已知限制（不是 bug，是边界）

1. **RNAComposer 无法自动提交**：官方无公开 API，本站只生成可粘贴输入。
2. **本地 3D 是拓扑级**：不表达碱基朝向、螺旋扭转；伪结只按距离约束近似，结果页会给提示。
3. **Rfam 注释静态维护**：只覆盖 5 条真实分子，不联网查询。
4. **RCSB 依赖外网**：不可达时降级为手动桥接，不影响其他功能。
5. **单进程**：任务表、限流、检索缓存都在内存，重启即丢；`--workers>1` 直接拒绝启动。
6. **无鉴权**：定位是单机/内网工具，公网部署需自行加反代与鉴权。线上演示实例的访问控制由托管平台的分享链接提供（首次带 `?sharecode=` 种 cookie），**不是应用自身的账号体系**。
7. **缓存量化**：嵌入向量写入时四舍五入到 3 位，首次与缓存命中的 PCA 存在 1e-3 级差异。

---

## 9. 部署与复现

### 9.1 四种启动方式

```bash
# ① 一键（Windows 双击 run.bat）
bash run.sh          # 建 .venv → pip install → 首次 npm ci + build → uvicorn 单端口托管 dist

# ② 开发模式（前后端分离热更新）
uvicorn backend.main:app --reload --port 8000
cd frontend && npm run dev        # 5173，/api 代理到 8000

# ③ 容器（API + 静态页面单容器）
docker compose up --build -d

# ④ 托管沙箱（线上演示形态：安装阶段建前端 + 读 PORT 启动，详见 §9.3）
```

`run.sh` 的四个阶段都是幂等的：`.venv` 已存在则复用，`frontend/dist/index.html` 已存在则跳过构建（删掉 `frontend/dist` 即可强制重建）。ViennaRNA 安装失败不会中断流程，只会降级。

### 9.2 容器设计

- 多阶段构建：`node:18-alpine` 构建前端 → `python:3.11-slim` 运行。
- **非 root**：`app` 用户 uid 10001；`HEALTHCHECK` 内置。
- 挂载：`./data` 只读、`./models` 只读，缓存写命名卷 `rna-cache:/app/cache`（避开 bind mount 属主问题）。
- `stop_grace_period: 30s` + lifespan 取消任务，保证长任务不被硬切。
- `GIT_COMMIT=$(git rev-parse --short HEAD) docker compose build` 把提交写进 OCI `LABEL`，便于追溯镜像对应的代码版本。

### 9.3 托管部署（在线演示实例）

线上实例跑在托管沙箱里：只给一个公网端口、单进程、无持久盘。与容器部署的差异只有三处，但每一处都会直接导致启动失败，照抄 compose 是不行的：

| 差异点 | 容器里的做法 | 托管沙箱里必须改成 |
|---|---|---|
| 前端产物 | Dockerfile 多阶段构建，把 `dist` 拷进镜像 | 源码上传会剔除 `dist/`（构建产物），需在安装阶段执行 `cd frontend && npm ci && npm run build` |
| 端口 | `CMD ... --port 8000` 写死 | 读平台注入的 `PORT`；`${PORT}` 在部分运行器里不展开，交给 Python 读环境变量 |
| 进程数 | `--workers 1` | 不变。任务表 / 限流 / 检索缓存是进程内状态，多进程启动即报错 |

启动命令：

```bash
python -c "import os,uvicorn;uvicorn.run('backend.main:app',host='0.0.0.0',port=int(os.environ.get('PORT','8000')),workers=1)"
```

线上实测（2026-11-13，远端请求，非本机）：

| 检查 | 结果 |
|---|---|
| `/api/health` | `engine=viennarna-python`、`viennarna_version=2.7.0`、`deep_max_len=300`、`model_3d_max_len=500` |
| `/api/fold` tRNA 76 nt | `mfe=-22.4`、`num_pairs=21`，dot-bracket 与本地逐字符一致 |
| `/api/pairprob` 9 nt | `engine=viennarna-bpp`（配分函数，非采样） |
| `/api/3d/predict` 76 nt | `engine=builtin`、`atoms=456`，秒级返回 |
| 前端路由 | `/`、`/fold`、`/mutate`、`/deep`、`/tertiary` 均 200（SPA 回退生效） |
| 深度层 | 预置示例命中随包上传的 `data/cache`（`source=cache`，640 维真实向量）；新序列因实例未装 torch / transformers 而失败，降级为 34 维确定性嵌入 |

> 沙箱无持久盘：缓存随实例重启清空，与 §8.2 第 5 条「重启即丢」是同一件事，不是额外退化。演示前建议先点一遍示例热身。

### 9.4 复现校验步骤（评审可直接执行）

```bash
python -m pytest tests -q                 # 期望 72 passed，约 2 s
cd frontend && npm test && npm run build  # 期望 5 passed + 构建成功
curl -s localhost:8000/api/health         # 期望 engine=viennarna-python、viennarna_version=2.7.0
curl -s -X POST localhost:8000/api/fold \
  -H 'Content-Type: application/json' \
  -d '{"sequence":"GCGGAUUUAGCUCAGUUGGGAGAGCGCCAGACUGAAGAUCUGGAGGUCCUGUGUUCGAUCCACAGAAUUCGCACCA"}'
# 期望 mfe=-22.4、num_pairs=21、structure 以 (((((((..(((( 开头
```

---

## 10. 测试与 CI

| 层 | 数量 | 耗时 | 覆盖 |
|---|---|---|---|
| 后端 `tests/test_api.py` | 49 | 1.87 s | 全部端点、错误码、限流、404/SPA、伪结、缓存复用、外部引擎回退 |
| 后端 `tests/test_units.py` | 23 | 0.16 s | 序列规范化、dot-bracket 往返、折叠合法性、缓存淘汰/键、任务取消、RCSB 解析 |
| 前端 `frontend/test` | 5 | 128 ms | 序列规范化与简并碱基识别 |
| 构建 | — | — | `vite build` 兼做语法校验 |

CI（`.github/workflows/ci.yml`）：Python 3.11 + Node 18，跑后端测试、前端测试、前端构建。ViennaRNA 装不上时允许降级（`|| echo`），因此 CI 不保证真实引擎，**真实引擎需在本地/容器验证**——这是 CI 的刻意取舍，不是疏漏。

测试不联网、不加载真实 RNA-FM 权重，所以能在任何机器上 2 秒跑完。

---

## 11. 演进路线（按收益排序）

| 优先级 | 事项 | 现状 | 预期收益 |
|---|---|---|---|
| P0 | PCA 换 numpy（或 `torch.linalg.eigh`） | 300 nt 缓存命中仍需 1.9 s | 深度页延迟降到 <100 ms |
| P1 | 深度任务进度改 SSE/WebSocket | 500 ms 轮询，1 次/秒冗余请求 | 减少空转请求，进度更顺滑 |
| P1 | 突变偏移 UI 增加说明 | `max_shift_position` 常不等于突变位点（实测量到第 66 位而突变在第 7 位），易被误读 | 避免"是不是算错了"的质疑 |
| P2 | 任务表外置（Redis/SQLite） | 重启丢任务、无法多进程 | 可横向扩展、任务可追溯 |
| P2 | 接入真实 3D 引擎（RhoFold+/SimRNA via `RNA_3D_CMD`） | 只有拓扑级内置模型 | 原子级精度 |
| P3 | 增加 lint/type 检查（ruff + eslint） | 目前无静态检查 | 提前拦截低级错误 |
| P3 | 前端 E2E（Playwright）进 CI | 只有单元测试 | 防止 UI 回归 |

---

## 12. 附录：文件地图

```text
backend/
  main.py            路由 + 中间件 + 校验 + 静态托管
  rna_fold.py        三级折叠引擎、bpp、Nussinov、Boltzmann 采样
  rna_fm.py          RNA-FM 惰性加载、降级嵌入、缓存、PCA、任务队列
  rna3d.py           伪结解析、约束松弛、骨架铺原子、外部引擎钩子
  rcsb.py            Search API + 重试 + LRU
  composer_api.py    RNAComposer 输入文本生成
frontend/src/
  views/Home.vue     输入 + 校验 + 示例库（645 行）
  views/Fold.vue     指标卡、圈图/弧线图、热力图、Rfam 注释（473 行）
  views/Mutate.vue   位点滑杆、ΔMFE 卡、双结构对比、自动深度（617 行）
  views/Deep.vue     PCA 轨迹、L2 曲线、突变偏移 Top5（639 行）
  views/Tertiary.vue 预置库、RCSB 结果、RNAComposer 桥接、本地建模（574 行）
  views/NotFound.vue 404 空态
  components/        FornaCircle（样式作用域隔离）、RnaArc、MolstarViewer
  sequence.js        前端序列规范化（与后端同规则）
data/
  examples/examples.json  9 条示例（序列、dot-bracket、MFE、PDB、Rfam）
  pdb/                    5 个实验结构坐标
tests/                    72 项后端测试
scripts/prewarm.py        演示预热：折叠 + 嵌入 + 补齐 PDB
```

> 维护提示：改动 `rna3d.py` 的几何算法后，务必把 `_BUILTIN_VERSION` 加 1，否则用户端会继续命中旧缓存模型。
