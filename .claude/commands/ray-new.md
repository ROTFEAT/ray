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

**版本检查：** 如果输出"有更新可用"，告知用户并建议更新。不阻断流程。

**环境检查：** 如果输出 `FAIL`，提示运行 `cd $RAY_DIR && ./setup` 交互式配置。**环境检查通过后才继续。**

**后续所有命令中的 `skills/` 路径都替换为 `$RAY_DIR/skills/`。**

## 你的工作流程

### 第一步：理解用户需求

询问用户想做什么。接受以下形式的输入：

- **自然语言描述**: "我想跑 1000 次蒙特卡洛模拟" → 你来生成完整脚本
- **已有 Python 脚本**: "帮我把这个脚本并行化" → 读取脚本，改造成 Ray 版本
- **参考模板**: "给我一个调参的模板" → 直接提供对应模板

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
