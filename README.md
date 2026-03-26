# Ray Skills

让你和同事通过 Claude Code 一句话把计算任务推到 Ray 集群上跑。零 Ray 经验也能用。

## 安装（一行）

```bash
git clone https://github.com/ROTFEAT/ray.git && cd ray && ./setup
```

安装过程会自动：
1. 安装 Python 依赖（ray, minio）
2. 交互式询问集群地址和 MinIO 密钥（找管理员要）
3. 验证集群连通性

## 使用教程

### 场景 1：我有一个想法，想在集群上跑

你只需要用自然语言描述，Claude 帮你写代码：

```
你: /ray-new
Claude: 你想做什么计算？
你: 我想用蒙特卡洛方法估算 π，跑 10000 次模拟
Claude: （自动生成完整的 Ray 任务脚本 → 保存到 tasks/）
Claude: 脚本已保存，用 /ray-push 提交到集群
你: /ray-push tasks/monte_carlo.py
Claude: （环境检查 → 验证脚本 → 提交 → 返回 Job ID）
```

### 场景 2：我有现成的 Python 脚本，想并行化

```
你: /ray-new
Claude: 你想做什么计算？
你: 帮我把 my_script.py 并行化，里面有个循环跑得太慢了
Claude: （读取脚本 → 分析循环逻辑 → 改造为 @ray.remote → 展示 diff → 解释每处修改）
你: /ray-push tasks/my_script_ray.py
```

### 场景 3：我要调参

```
你: /ray-new
Claude: 你想做什么计算？
你: 我有一个函数，想搜索最优参数组合，大概 20 个参数
Claude: （推荐 Ray Tune + Optuna 模板 → 生成调参脚本 → 解释代码）
你: /ray-push tasks/tune_task.py --pip optuna
```

### 场景 4：任务提交后

```
# 提交后会打印 Job ID 并自动保存到本地
# 可以安全关闭电脑——任务在集群上跑，不依赖你的机器

# 回来后查看状态
你: /ray-status
Claude: （显示集群状态 + 你的任务列表）

# 或者用 CLI
python skills/ray_job.py --list              # 查看所有任务（含本地提交历史）
python skills/ray_job.py --status <job_id>   # 单个任务状态
python skills/ray_job.py --logs <job_id>     # 查看日志
python skills/ray_job.py --result <job_id>   # 下载结果到 output/
python skills/ray_job.py --stop <job_id>     # 停止任务
```

### 场景 5：依赖很多，每次安装很慢

```
# 预构建镜像（需要 Docker）
python skills/build_image.py --name ml-env --pip "scikit-learn xgboost pandas optuna"

# 之后提交秒启动
python skills/ray_job.py tasks/my_task.py --image ml-env --wait
```

## Skill 一览

| Skill | 用途 | 适合谁 |
|-------|------|--------|
| `/ray-new` | 从零生成 Ray 任务脚本，或把现有脚本并行化 | 所有人（新手从这里开始） |
| `/ray-push` | 验证脚本 + 提交到集群 | 所有人 |
| `/ray-status` | 查集群状态、任务状态、拉结果、看日志 | 所有人 |

## 工作流

```
/ray-new → 生成脚本 → /ray-push → 提交执行 → /ray-status → 拿结果
```

每个 skill 运行前会自动检查环境和版本更新，无需手动维护。

## 更新

```bash
python skills/update_check.py --update
# 或
cd ray && git pull
```

## 项目结构

```
ray/
├── setup                         # 一行安装脚本
├── .env.example                  # 配置模板
├── .claude/commands/             # Claude Code skill 定义
│   ├── ray-new.md                # 生成任务脚本
│   ├── ray-push.md               # 验证 + 提交
│   └── ray-status.md             # 监控 + 结果
├── skills/                       # Python 工具
│   ├── ray_job.py                # 任务提交 CLI
│   ├── check_env.py              # 环境检查
│   ├── update_check.py           # 版本更新检查
│   ├── minio_io.py               # MinIO 读写
│   ├── build_image.py            # Docker 镜像构建
│   ├── config.py                 # 配置加载
│   └── template_task.py          # 任务模板（含 save_result）
└── tasks/                        # 任务脚本目录
    ├── stress_test.py            # 集群压测示例
    └── test_optuna.py            # Optuna 调参示例
```

## FAQ

**Q: 提交后关电脑任务还在吗？**
A: 在。任务在集群上跑，和你的电脑无关。Job ID 自动保存到本地 `.jobs/history.jsonl`，回来后用 `/ray-status` 找到。

**Q: 我完全不会 Ray 怎么办？**
A: 用 `/ray-new`，告诉 Claude 你想算什么，它会帮你生成完整代码并用中文解释每段逻辑。

**Q: 怎么拿回结果？**
A: 任务脚本里的 `save_result()` 会把结果存到 MinIO。用 `python skills/ray_job.py --result <job_id>` 下载到本地 `output/` 目录。

**Q: 集群有多少资源？**
A: 运行 `/ray-status` 查看实时集群状态。

**Q: 密钥从哪里获取？**
A: 找集群管理员要 MINIO_ACCESS_KEY 和 MINIO_SECRET_KEY，运行 `./setup` 时交互输入。
