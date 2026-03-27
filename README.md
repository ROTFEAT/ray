# Ray Compute

248 CPU 共享计算集群。AI 在对话中透明调用，不中断推理。零依赖安装。

## 安装

```bash
git clone https://github.com/ROTFEAT/RayCompute.git && cd ray && ./setup
```

不需要装任何 pip 包。`setup` 交互式配置集群地址和密钥，自动写入 `~/.claude/CLAUDE.md`。

安装后 AI 在任何项目的对话中都知道"我有一个 248 CPU 集群可用"，需要大量计算时自动调用。

## 工作方式

你不需要手动操作。AI 自己判断何时需要集群：

```
你: "帮我找到最优止损参数，在 1-10 之间搜索"

AI 思考: 需要跑多组回测，用集群并行
AI 行动: python skills/ray_compute.py exec <<'PYEOF'
         import ray, json
         @ray.remote
         def backtest(stop_loss): ...
         ray.init()
         results = ray.get([backtest.remote(sl) for sl in range(1, 11)])
         print(json.dumps(results))
         PYEOF
AI 分析: stop_loss=3 最优，Sharpe 1.45
AI 回复: "最优止损是 3%，Sharpe 1.45。以下是完整分析..."
```

一个对话里可以多次调用。AI 看结果 → 调参数 → 再跑 → 循环到满意。

## 手动使用

```bash
# 执行脚本
python skills/ray_compute.py run tasks/my_task.py --pip numpy

# 从 stdin 执行
python skills/ray_compute.py exec <<'PYEOF'
import ray, json
ray.init()
# ...
PYEOF

# 异步提交（长任务）
python skills/ray_compute.py submit tasks/my_task.py

# 查询结果
python skills/ray_compute.py result <job_id>

# 集群状态
python skills/check_env.py
```

所有命令返回 JSON。结果自动下载到 `ray-result/` 目录。

## 更新

```bash
python skills/update_check.py --update
```
