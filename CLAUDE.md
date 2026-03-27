# Ray 集群计算资源

## 你有一个 248 CPU 的 Ray 集群可用

6 节点，1.9TB 内存，Python 3.12，无 GPU。

当你需要执行以下计算时，**用集群而非本地**：
- 超参数搜索 / 参数优化（> 10 组参数）
- 蒙特卡洛模拟（> 100 次）
- 批量回测 / 批量评估
- 大数据集并行处理
- 任何预计超过 1 分钟的计算

## 调用方式

```bash
# 执行已有脚本（同步等待结果）
python skills/ray_compute.py run tasks/xxx.py --pip minio --timeout 300

# 从 stdin 执行（你生成的代码直接传入）
python skills/ray_compute.py exec --pip minio <<'PYEOF'
import ray
import json

@ray.remote(num_cpus=1)
def compute(params):
    # 你的计算逻辑
    return {"params": params, "result": ...}

ray.init()
results = ray.get([compute.remote(p) for p in param_list])
print(json.dumps(results))
ray.shutdown()
PYEOF

# 异步提交（长任务，> 10 分钟）
python skills/ray_compute.py submit tasks/xxx.py --pip minio

# 查询异步结果
python skills/ray_compute.py result <job_id>
```

**所有输出都是 JSON**，你可以直接解析。

## 规则

- **不要问用户"要不要用集群"**——你自己判断。需要大量计算就用。
- **不要用 /ray-new 或 /ray-push**——直接调 `ray_compute.py`。
- **拿到结果后分析**，不满意就调整参数再跑。一个 session 里可以多次调用。
- **数据大于 50MB 先上传到 MinIO**：`python skills/data_upload.py upload <file> --name <path>`
- **显式指定 --pip 依赖**，不要依赖自动检测。
- **短任务用 `run`**（同步），长任务用 `submit`（异步）+ `result`（轮询）。
- **超时设置**：默认 300s，中等任务设 `--timeout 600`，超过 10 分钟用异步。

## Ray 代码规范

```python
import ray
ray.init()  # 不带参数！

@ray.remote(num_cpus=1)
def compute(task_id, params):
    return {"task_id": task_id, "result": ...}

# 批量提交，然后统一 ray.get（不在循环里 get）
futures = [compute.remote(i, p) for i, p in enumerate(params)]
results = ray.get(futures)

# 结果输出为 JSON（ray_compute.py 会解析最后一行 JSON）
import json
print(json.dumps(results))

ray.shutdown()
```

**注意：**
- `from ray.tune import RunConfig`（不是 ray.train 或 ray.air）
- 不用 `plt.show()`、`input()`、`multiprocessing`
- 不用硬编码本地路径
- 大对象用 `ray.put()` 一次放入 object store
- 单任务内存密集型（scipy DE + 高维度）应在 driver 本地跑，只把小任务发到 Worker

## 手动查看集群状态

```bash
# 用 /ray-status skill 或直接：
python skills/check_env.py
python skills/ray_job.py --list
```
