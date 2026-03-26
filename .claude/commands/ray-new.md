# /ray-new - 生成 Ray 集群任务脚本

帮助用户（包括零 Ray 经验的用户）生成可以在 Ray 集群上运行的 Python 脚本。用户只需要描述想做什么，你来生成完整的 Ray 任务代码。

## 集群信息

- Ray 版本: 2.54.0, Python 3.12
- 集群规模: 248 CPU, 2TB+ 内存, 6 节点
- 无 GPU
- 结果通过 MinIO 持久化

## 前置检查（每次必须执行）

在做任何事之前，先确定项目路径，然后运行检查：

```bash
RAY_DIR=$(cat ~/.claude/.ray-skills-path 2>/dev/null || echo "")
echo "RAY_DIR: $RAY_DIR"
```

如果 `RAY_DIR` 为空，说明未安装。提示用户运行 `git clone https://github.com/ROTFEAT/ray.git && cd ray && ./setup`。

```bash
python $RAY_DIR/skills/update_check.py
python $RAY_DIR/skills/check_env.py
```

**如果全部通过：不要输出任何检查结果，静默继续下一步。**
**如果有更新：** 一句话告知，不阻断。
**如果 FAIL：** 提示运行 `cd $RAY_DIR && ./setup`。

**重要：前置检查是内部流程，通过时不要告诉用户"环境检查通过"、"版本最新"之类的话。直接进入第一步。**

**后续所有命令中的 `skills/` 路径都替换为 `$RAY_DIR/skills/`。**

## 你的工作流程

### 第一步：理解用户需求（先扫描，再问）

**静默扫描上下文，不要向用户描述扫描过程：**

1. 读取命令参数（用户可能在 `/ray-new` 后面写了需求）
2. **查找设计文档（关键！）** — 用户很可能用 `/office-hours` 等工具生成过设计文档：
   - 搜索 `~/.gstack/projects/` 目录下最近的 `*-design-*.md` 文件
   - 搜索当前项目中的 `*.md` 文件（排除 README/CLAUDE.md），找含有"设计"、"计划"、"需求"、"参数"、"数据"关键词的
   - 搜索 `tasks/plan-*.md`（之前 `/ray-new` 生成的计划文件）
   - 如果找到设计文档，**读取它**——里面通常有完整的需求描述、数据结构、计算逻辑、参数空间、技术选型，这些信息直接用于生成 Ray 脚本
3. 扫描当前项目（`CLAUDE.md`、`README.md`、`tasks/`、最近修改的 `.py`）
4. 查看 git 上下文（`git log --oneline -5`、`git diff --stat`）
5. 检查对话上下文（用户之前提到的需求、脚本、数据）

**如果找到了设计文档，以它为主要输入**——文档里的需求描述、数据结构、计算逻辑比用户口头描述更完整准确。生成建议时引用文档内容：
```
你在设计文档 <文件名> 中描述了 [需求概要]。
数据来源: [文档中的数据描述]
计算逻辑: [文档中的核心逻辑]
建议按这个方案生成 Ray 脚本。确认？
```

**重要：扫描过程不要输出给用户看。不要说"让我扫描项目"、"正在读取上下文"之类的话。直接给出建议。**

根据扫描结果，直接提出建议：
```
你的项目有 [xxx.py]，核心逻辑是 [描述]。
建议：[具体方案]。要按这个方向来吗？
```

**只有完全无法判断时**才问用户想做什么。接受以下形式的输入：

- **自然语言描述**: "我想跑 1000 次蒙特卡洛模拟" → 你来生成完整脚本
- **已有 Python 脚本**: "帮我把这个脚本并行化" → 读取脚本，改造成 Ray 版本
- **参考模板**: "给我一个调参的模板" → 直接提供对应模板

### 第 1.5 步：数据源检测（关键！）

理解用户需求后，**必须**判断任务是否涉及数据：

**检测清单：**
1. 用户提到了数据文件（CSV、Excel、Parquet、JSON）？
2. 用户的脚本里有数据库连接（`mysql://`、`postgresql://`、`sqlite:///`、`pymysql`、`psycopg2`、`sqlalchemy`）？
3. 用户提到了数据量（"一亿条"、"几个 GB"、"大量数据"）？
4. 用户的脚本里读取了本地文件路径（`pd.read_csv("./data.csv")`、`open("data.json")`）？

**如果检测到数据源，先检查 MinIO 是否已有数据（session 可能中断过，数据可能已上传）：**

```bash
python $RAY_DIR/skills/data_upload.py ls <项目名>/
```

如果已有数据文件，显示给用户确认：
```
MinIO 中已有以下数据：
  ray-data/<项目名>/trades.parquet  (850 MB, 2026-03-26 20:30)

这份数据可以直接使用，无需重新上传。确认使用？
```

用户确认后直接跳到脚本生成，不重复上传。**只有 MinIO 中没有数据时才执行上传流程：**

#### 情况 A：本地文件（CSV/Parquet/JSON）
```
如果文件 < 50MB → 可以通过 working_dir 直接提交，无需上传
如果文件 >= 50MB → 必须先上传到 MinIO：
  python $RAY_DIR/skills/data_upload.py upload <文件路径> --name <项目名>/<文件名>
```

#### 情况 B：本地数据库（最常见的场景）
用户的数据在本地 MySQL/PostgreSQL/SQLite 中，集群无法直接访问。流程：
1. 确认数据库连接信息（类型、地址、库名、表名）
2. 确认要导出的数据（整表？还是带条件查询？）
3. 确认数据结构（哪些列？数据类型？大致行数？）
4. 用 data_upload.py 导出到 MinIO：
```bash
python $RAY_DIR/skills/data_upload.py db "<连接字符串>" --table <表名> --name <项目名>/<表名>.parquet
# 或带查询条件
python $RAY_DIR/skills/data_upload.py db "<连接字符串>" --query "SELECT * FROM trades WHERE date > '2024-01-01'" --name <项目名>/trades.parquet
```
5. 导出完成后会打印 schema 信息（列名、数据类型、行数）
6. **生成的 Ray 脚本中，读取数据的方式必须改为从 MinIO 读取**（不能连本地数据库）

#### 情况 C：远程数据库（集群可直接访问）
如果数据库在集群同一内网（如 192.168.3.x），Ray 任务可以直接连接。但注意：
- 大量并发读取可能压垮数据库
- 建议仍然先导出到 MinIO，按需分片读取

**数据搬迁完成后，告知用户：**
```
数据已上传到 MinIO:
  bucket: ray-data
  路径:   <项目名>/<文件名>
  行数:   X,XXX,XXX
  大小:   XXX MB
  列:     col1 (int64), col2 (float64), col3 (object), ...

生成的 Ray 脚本将从 MinIO 读取数据，不再依赖你的本地数据库。
```

#### 生成脚本中的数据读取模板

如果数据已上传到 MinIO，生成的脚本必须用以下方式读取：

```python
def load_data_from_minio():
    """从 MinIO 加载数据（在集群 Worker 上执行）"""
    from minio import Minio
    import pandas as pd
    import io
    import os

    client = Minio(
        os.environ.get("MINIO_ENDPOINT", ""),
        access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
        secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
        secure=False
    )
    resp = client.get_object("ray-data", "<项目名>/<文件名>")
    df = pd.read_parquet(io.BytesIO(resp.read()))
    print(f"加载 {len(df):,} 行数据")
    return df
```

**注意：如果数据很大（> 1GB），不要在每个 Worker 都加载全量数据。应该：**
- 在 driver 里加载一次，用 `ray.put(df)` 放入 object store
- 或者按分片加载，每个 task 只处理一部分

### 第二步：选择任务模式

根据用户需求，用以下决策树选择模板：

- 用户提到"调参"/"超参数"/"搜索最优"/"optimize" → **B) 调参搜索**
- 用户提到"模型"/"推理"/"预测"/"inference" → **E) 批量推理**
- 用户提到"共享状态"/"计数器"/"全局变量"/"需要 worker 间通信" → **D) Actor 模式**
- 用户提到"CSV"/"数据集"/"清洗"/"ETL"/"大文件" → **C) 数据处理**
- 其他（默认） → **A) 简单并行**

向用户确认选择：

```
根据你的描述，推荐使用 [模式名称]。

A) 简单并行 — 同一个函数跑 N 次不同参数（最常用）
B) 调参搜索 — 超参数优化（Ray Tune + Optuna）
C) 数据处理 — 大数据集分片并行处理（Ray Data）
D) Actor 模式 — 需要 worker 间共享状态
E) 批量推理 — 模型批量预测

推荐: [X]，是否同意？
```

### 第 2.5 步：生成计划文件（必须）

选定模式后，**先生成计划文件，等用户确认后再写代码。** 计划文件保存到 `tasks/` 目录：

文件名格式：`tasks/plan-<简短描述>.md`

```markdown
# Ray 任务计划: <标题>

生成时间: <日期>
状态: 待确认

## 目标
<一句话描述这个任务要做什么>

## 数据
- 来源: <本地文件 / MySQL / PostgreSQL / 无数据>
- 规模: <行数、大小>
- 存储: <已在 MinIO / 需要上传 / 不涉及数据>
- 关键字段: <列名和类型，帮用户确认数据结构是否正确>

## 计算逻辑
- 模式: <简单并行 / 调参搜索 / 数据处理 / Actor / 批量推理>
- 核心函数: <要并行化的计算逻辑概述>
- 参数空间: <如果是调参，列出参数名、范围、类型>

## 资源预估
- 集群: 248 CPU, 2TB 内存
- 并发: <预计同时跑多少个 task>
- 单任务耗时: <预估>
- 总预估时间: <总任务数 / 并发数 × 单任务耗时>

## 依赖
- Python 包: <需要 --pip 安装的包>
- 数据搬迁: <是否需要先上传数据到 MinIO>

## 执行步骤
1. <数据准备（如需要）>
2. <生成脚本>
3. <提交到集群>
4. <预计完成时间，拉取结果>

## 输出
- 结果保存到: MinIO ray-result/<job_id>/
- 输出格式: <JSON / CSV / Parquet>
- 包含内容: <结果包含什么字段>
```

**生成后询问用户：**
```
任务计划已保存到 tasks/plan-xxx.md，请确认：
1. 数据结构是否正确？（特别是列名和类型）
2. 计算逻辑是否符合预期？
3. 参数空间是否需要调整？

确认后我开始生成 Ray 脚本。
```

**用户确认后才进入第三步。如果用户要修改，更新计划文件后重新确认。**

### 第三步：生成任务脚本

基于用户需求和选定模板，生成完整可运行的 Python 脚本。**所有脚本必须包含以下结构：**

```python
import ray

# 1. 结果保存函数（必须在所有 remote 函数之前定义）
def save_result(data, filename="result.json"):
    """保存结果到 MinIO"""
    from minio import Minio
    import json, io, os
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    access_key = os.environ.get("MINIO_ACCESS_KEY", "")
    secret_key = os.environ.get("MINIO_SECRET_KEY", "")
    bucket = os.environ.get("MINIO_BUCKET", "ray-result")
    job_id = os.environ.get("RAY_JOB_ID", "unknown")
    object_name = f"jobs/{job_id}/{filename}"
    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    buf = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    client.put_object(bucket, object_name, io.BytesIO(buf), len(buf), "application/json")
    print(f"结果已保存: {bucket}/{object_name}")
    print(f"拉取命令: python skills/ray_job.py --result {job_id}")

# 2. @ray.remote 修饰的计算函数
@ray.remote(num_cpus=1)
def compute(task_id, params):
    """单个计算任务"""
    # 用户的计算逻辑
    return result

# 3. main 函数
def main():
    ray.init()  # 不带参数！

    # 批量提交
    futures = [compute.remote(i, p) for i, p in enumerate(params)]
    results = ray.get(futures)  # 批量获取，不在循环里 get

    # 保存结果
    save_result(results)
    ray.shutdown()

if __name__ == "__main__":
    main()
```

**生成时的规则：**

1. `ray.init()` 不带任何参数
2. `ray.get()` 放在所有任务提交之后，批量获取
3. 大对象先 `ray.put()` 再传引用
4. 不用 `plt.show()`，用 `plt.savefig()`
5. 不用 `input()`
6. 不引用本地文件路径（如 `/Users/xxx/`）
7. 不用 `multiprocessing`
8. 不用 `num_gpus`（集群无 GPU）
9. 确保 `save_result()` 在脚本中，方便拿回结果
10. 只用 Python 3.12 兼容的语法

### 第 3.5 步：脚本质量验证（生成后必须执行）

脚本生成后，**不要直接交给用户**，先经过三层验证：

#### 层 1：自动检查（立即执行）

```bash
# 语法检查
python -c "import ast; ast.parse(open('<脚本路径>').read())" && echo "SYNTAX_OK" || echo "SYNTAX_ERROR"
```

同时自查以下清单：

**阻断项（有一项不通过就修复）：**
- [ ] `ray.init()` 不带参数
- [ ] 所有 `ray.get()` 在批量提交之后
- [ ] 没有 `plt.show()`、`input()`、`multiprocessing`
- [ ] 没有硬编码本地路径
- [ ] `save_result()` 存在且被调用
- [ ] 所有第三方 import 都列入了依赖
- [ ] 请求资源不超集群上限（num_cpus <= 248, num_gpus = 0）
- [ ] 数据读取从 MinIO 而不是本地文件/数据库

**优化项（建议但不阻断）：**
- [ ] 大对象用了 `ray.put()`
- [ ] 有错误处理（try/except 在关键位置）
- [ ] 有进度输出（print 让用户知道在跑）
- [ ] `ray.shutdown()` 在最后

发现阻断项 → 自动修复 → 重新检查。最多 3 轮，3 轮修不好则告知用户需要手动检查。

#### 层 2：独立评审（子代理）

用 Agent 工具派发一个独立评审子代理，给它**只有脚本文件和计划文件**（不给生成过程的推理），让它独立判断：

子代理 prompt：
```
读取 <脚本路径> 和 <计划文件路径>。
你是 Ray 集群脚本评审员，目标集群: 248 CPU, 2TB 内存, 无 GPU, Python 3.12。
检查：
1. 脚本是否完整实现了计划文件中的所有需求？逐项对比。
2. 有哪些方式会让脚本在集群上失败？（序列化、内存、依赖、路径）
3. 性能问题：任务粒度是否合理？数据传输是否最优？
4. 对每个问题给出: 严重度(阻断/建议) + 具体修复方案
```

子代理返回 PASS 或带修复建议的 ISSUES 列表。

#### 层 3：跨模型验证（可选，Codex）

```bash
which codex 2>/dev/null && echo "CODEX_AVAILABLE" || echo "CODEX_NOT_AVAILABLE"
```

如果 Codex 可用，问用户是否要第二意见（2-3 分钟）。如果用户同意：

```bash
codex exec "读取 <脚本路径>。这个 Ray 脚本要跑在 248 CPU 集群上。像混沌工程师一样找到所有会失败的方式：资源耗尽、序列化错误、依赖缺失、死锁、静默错误。只列问题，不给表扬。" -s read-only
```

#### 综合处理

```
层 1 阻断项 → 自动修复，不问用户
层 2 阻断项 → 修复后重新过层 1
层 2 建议项 + 层 3 发现 → 汇总展示给用户，由用户决定是否修复

验证结果：
  层 1: PASS（语法 ✓, 8/8 检查项通过）
  层 2: PASS（子代理确认脚本完整实现计划需求）
  层 3: 1 个建议（Codex: "数据量大时建议加 --memory 参数"）

脚本质量: ✅ 可提交
```

**只有层 1 和层 2 都 PASS 后才进入下一步。**

### 第四步：逐段解释代码

用中文向用户解释生成的代码，**假设用户完全不懂 Ray**：

```
## 代码解释

**import ray** — Ray 是一个分布式计算框架，让你的代码能在多台机器上同时运行。

**@ray.remote** — 这个装饰器告诉 Ray："这个函数可以发到集群的任意一台机器上执行"。
加了这个之后，调用方式从 compute(1, params) 变成 compute.remote(1, params)，
返回的不是结果本身，而是一个"未来结果"的引用（ObjectRef）。

**ray.init()** — 连接到 Ray 集群。不带参数时，Ray 会自动从环境变量找到集群地址。

**futures = [compute.remote(...) for ...]** — 一口气把所有任务提交到集群。
这些任务会被分配到不同的机器上并行执行。我们提交了 N 个任务，集群有 248 个 CPU，
所以最多 248 个任务同时跑。

**results = ray.get(futures)** — 等待所有任务完成，收集结果。
注意：我们是在所有任务都提交之后才统一等待，而不是提交一个等一个。
这样才能让 248 个 CPU 同时工作。

**save_result(results)** — 把结果保存到 MinIO 对象存储。
因为计算是在集群机器上跑的，结果不在你本地。
save_result 会把结果存到 MinIO，之后你可以用 --result 命令拉回来。
```

根据实际生成的代码调整解释内容。关键是让用户理解"为什么这样写"。

### 第五步：保存并提示下一步

1. 询问用户要把脚本保存到哪个路径（默认建议 `tasks/` 目录）
2. 保存脚本
3. 提示：

```
脚本已保存到 <路径>

下一步：使用 /ray-push 提交到集群执行
或者直接运行: python skills/ray_job.py <脚本路径> --pip minio --wait
```

## 五种模板的具体设计

### A) 简单并行 (parallel_map)

适用：同一个函数跑 N 组不同参数

```python
@ray.remote(num_cpus=1)
def compute(task_id, params):
    # 用户的计算逻辑
    return {"task_id": task_id, "result": ...}

futures = [compute.remote(i, p) for i, p in enumerate(param_list)]
results = ray.get(futures)
```

### B) 调参搜索 (tune_optuna)

适用：超参数优化，搜索最优参数组合

```python
from ray import tune
from ray.tune.search.optuna import OptunaSearch

def objective(config):
    # 用户的目标函数
    return {"loss": ...}

tuner = tune.Tuner(
    objective,
    param_space={...},
    tune_config=tune.TuneConfig(
        search_alg=OptunaSearch(metric="loss", mode="min"),
        num_samples=1000,
        max_concurrent_trials=248,
    ),
)
results = tuner.fit()
```

提醒用户：需要 `--pip optuna` 依赖

### C) 数据处理 (data_pipeline)

适用：大数据集分片并行处理

```python
import ray.data

ds = ray.data.read_csv("s3://bucket/data.csv")  # 或本地大文件
ds = ds.map(transform_fn)
ds = ds.filter(filter_fn)
ds.write_csv("/tmp/output/")
```

### D) Actor 模式 (actor_service)

适用：需要 worker 间共享可变状态

```python
@ray.remote
class Counter:
    def __init__(self):
        self.count = 0
    def increment(self):
        self.count += 1
        return self.count

counter = Counter.remote()
refs = [counter.increment.remote() for _ in range(100)]
results = ray.get(refs)
```

### E) 批量推理 (batch_inference)

适用：模型批量预测

```python
@ray.remote(num_cpus=1)
def predict(model_bytes, batch):
    import pickle
    model = pickle.loads(model_bytes)
    return [model.predict(x) for x in batch]

# 模型只传一次到 object store
model_ref = ray.put(pickle.dumps(model))
# 数据分批
batches = [data[i:i+100] for i in range(0, len(data), 100)]
futures = [predict.remote(model_ref, b) for b in batches]
results = ray.get(futures)
```

## 如果用户提供了已有脚本

读取用户的脚本，分析：

1. **核心计算逻辑是什么？** 哪个函数/循环是需要并行化的部分
2. **数据流是什么？** 输入从哪来，输出到哪去
3. **有哪些依赖？** 需要的第三方包

然后：
1. 把核心计算逻辑提取为 `@ray.remote` 函数
2. 把串行循环改为批量 `remote()` 调用
3. 添加 `ray.init()` 和 `ray.shutdown()`
4. 添加 `save_result()` 保存结果
5. 用 diff 格式展示改动，解释每处修改的原因

## 注意事项

- 集群 Python 3.12，不要用 3.13+ 语法
- numpy 数组从 object store 取出是只读的，修改前需 `.copy()`
- 不要在 remote 函数里 `return ray.put(x)`，直接 `return x`
- 每个任务至少毫秒级计算量，太细的任务调度开销大于收益
