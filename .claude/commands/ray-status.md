# /ray-status - Ray 集群与任务监控

查看 Ray 集群状态、管理任务、拉取结果。根据用户意图自动路由到对应操作。

## 集群信息

- 地址和密钥从 `.env` 文件读取（运行 `./setup` 配置）
- 提交工具: `python skills/ray_job.py`

## 前置检查（每次必须执行）

在做任何事之前，先确定项目路径，然后运行检查：

```bash
RAY_DIR=$(cat ~/.claude/.ray-skills-path 2>/dev/null || echo "")
echo "RAY_DIR: $RAY_DIR"
```

如果 `RAY_DIR` 为空，说明未安装。提示用户运行 `git clone https://github.com/ROTFEAT/RayCompute.git && cd RayCompute && ./setup`。

```bash
python $RAY_DIR/skills/update_check.py
python $RAY_DIR/skills/check_env.py
```

**如果全部通过：不要输出任何检查结果，静默继续。不要说"环境检查通过"。**
**如果有更新：** 一句话告知，不阻断。
**如果 FAIL：** 提示运行 `cd $RAY_DIR && ./setup`。

**后续所有命令中的 `skills/` 路径都替换为 `$RAY_DIR/skills/`。**

## 意图路由

根据用户输入自动判断要做什么：

| 用户说 | 操作 |
|--------|------|
| "集群情况"/"集群状态"/"节点" | → 集群健康检查 |
| "我的任务"/"任务列表"/"在跑什么" | → 列出任务 |
| "任务状态" + job_id | → 查看单个任务 |
| "日志" + job_id | → 查看任务日志 |
| "拿结果"/"下载结果" + job_id | → 拉取结果 |
| "停掉"/"取消" + job_id | → 停止任务 |
| "失败了"/"报错了" + job_id | → 分析失败原因 |
| 无具体指令 | → 先查集群状态，再列出最近任务 |

## 操作详情

### 集群健康检查

```bash
# 检查集群是否在线
curl -s http://${RAY_DASHBOARD_URL}/api/version

# 获取节点信息
curl -s http://${RAY_DASHBOARD_URL}/api/cluster_status
```

输出格式：

```
## Ray 集群状态

状态: 在线 ✓
版本: Ray 2.54.0
节点: 6 个（1 Head + 5 Worker）
总 CPU: 248
已用 CPU: <从 API 获取>
可用 CPU: <计算>
总内存: <从 API 获取>
已用内存: <从 API 获取>

Dashboard: http://${RAY_DASHBOARD_URL}
```

如果集群不可达，提示：
- 检查网络连接（是否在同一内网）
- 联系集群管理员

### 列出任务

```bash
python skills/ray_job.py --list
```

展示最近的任务列表，包含 Job ID、状态、提交时间。

### 查看单个任务状态

```bash
python skills/ray_job.py --status <job_id>
```

如果用户没有给 job_id，先用 `--list` 列出最近任务，让用户选择。

### 查看任务日志

```bash
python skills/ray_job.py --logs <job_id>
```

读取日志后：
- 如果包含 `结果已保存` → 告知用户任务已完成，提示拉取结果
- 如果包含错误信息 → 进入失败分析模式

### 拉取任务结果

```bash
python skills/ray_job.py --result <job_id>
```

结果下载到 `output/<job_id>/` 目录。下载后：
- 如果是 JSON → 读取并展示前几条记录的摘要
- 如果是 CSV → 读取并展示 shape、列名、前几行

### 停止任务

```bash
python skills/ray_job.py --stop <job_id>
```

**停止前必须确认：** "确定要停止任务 <job_id> 吗？停止后无法恢复。"

### 失败分析

当用户说任务失败了，或者查看状态发现任务是 FAILED：

1. 先拉取日志：`python skills/ray_job.py --logs <job_id>`
2. 分析错误类型：

| 错误模式 | 常见原因 | 修复建议 |
|---------|---------|---------|
| `ModuleNotFoundError` | 缺少依赖 | 重新提交时加 `--pip <package>` |
| `MemoryError` / `OOM` | 单任务数据太大 | 减小批次大小或增加 `memory` 参数 |
| `RayTaskError` | 任务函数内部异常 | 查看 traceback，修复代码 |
| `ObjectStoreFullError` | object store 满了 | 减少中间数据量，用 `ray.put()` 复用 |
| `No available node` | 资源不足 | 减少 `num_cpus` 或等待其他任务完成 |
| `Timeout` | 任务超时 | 检查是否有死循环或网络阻塞 |
| `SerializationError` | 对象不可序列化 | 检查传给 remote 的参数类型 |

3. 给出具体修复建议和修改后的提交命令

## 注意事项

- 如果用户不知道 job_id，用 `--list` 帮他找
- Dashboard 链接是备选方案：http://${RAY_DASHBOARD_URL}
- 结果在 MinIO 中按 `jobs/<job_id>/` 组织
