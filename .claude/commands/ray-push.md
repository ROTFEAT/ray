# /ray-push - Ray 集群任务提交

用户想要将 Python 脚本提交到 Ray 集群执行。你需要先检查环境、验证脚本是否适合 Ray 集群，然后提交。

## 集群信息

- Ray 版本: 2.54.0, Python 3.10
- 集群规模: 248 CPU, 2TB+ 内存, 6 节点（1 Head + 5 Worker）
- Head 不跑任务（--num-cpus=0），248 CPU 全在 Worker
- 无 GPU
- 所有地址和密钥从 `.env` 文件读取（运行 `./setup` 配置）

## 你的工作流程

### 第零步：版本检查 + 环境检查（每次必须执行）

在做任何事之前，依次运行：

```bash
python skills/update_check.py
python skills/check_env.py
```

**版本检查：** 如果输出"有更新可用"，告知用户并建议运行 `python skills/update_check.py --update` 更新。不阻断流程，用户可以选择稍后更新。

**环境检查：** 如果输出 `FAIL`：
- `.env` 不存在 → 提示运行 `./setup`
- 变量为占位符（如 `your_access_key`）→ 告知用户需要填入真实值，运行 `./setup` 可交互式配置
- Ray CLI 未安装 → `pip install 'ray[default]'`
- minio 包缺失 → `pip install minio`
- 集群/MinIO 不可达 → 确认是否在同一内网

**环境检查通过后才继续。不要跳过。**

### 第一步：确认目标脚本

按以下优先级自动确定要提交的脚本，**不要让用户手动选择**：

1. 用户在命令参数中指定了路径 → 直接使用
2. 当前对话中刚用 `/ray-new` 生成了脚本 → 自动使用该脚本
3. 用户在对话中提到了某个脚本文件 → 自动使用
4. `tasks/` 目录下最近修改的 `.py` 文件 → 确认后使用
5. 以上都没有 → 询问用户

如果用户说"不知道怎么写 Ray 任务"或"帮我并行化"，建议使用 `/ray-new` 先生成任务脚本。

### 第二步：验证脚本适配性

读取脚本内容后，按以下检查清单逐项验证。将问题分为 **阻断项（必须修复）** 和 **建议项（建议优化）**。

#### 阻断项（有任何一项不通过则不能提交）

1. **无并行性**: 脚本没有使用 `@ray.remote`、`ray.remote()`、`ray.tune`、`ray.data` 等并行 API，纯串行代码无法受益于集群
2. **交互式输入**: 使用了 `input()`、`sys.stdin`、`getpass` 等 — 集群 worker 没有终端
3. **GUI 显示**: 使用了 `plt.show()`、`tkinter`、`cv2.imshow()` 等 — 集群无显示器。应改为 `plt.savefig()` 保存文件
4. **不可序列化对象作为参数**: `threading.Lock`、`sqlite3.Connection`、文件句柄、socket 等不能在 worker 间传递
5. **直接 multiprocessing/fork**: 使用 `multiprocessing.Process`、`os.fork()` 与 Ray 调度冲突
6. **硬编码本地路径**: 引用了不存在于集群节点的本地文件路径（如 `/Users/xxx/data.csv`）
7. **缺少 ray.init()**: 脚本没有调用 `ray.init()`
8. **请求 GPU 但集群无 GPU**: 使用了 `num_gpus > 0`
9. **Python 3.11+ 语法**: 使用了 `ExceptionGroup`、`TaskGroup`、`tomllib`、`type` 语句等 3.11+ 特性 — 集群只有 Python 3.10
10. **minio 依赖未声明**: 使用了 `save_result()` 或 `from minio import` 但没把 `minio` 加入依赖列表
11. **ray.init() 写了固定地址**: `ray.init("ray://...")` 会绕过 Jobs API。应使用 `ray.init()` 不带参数

#### 建议项（不阻断提交，但建议优化）

1. **ray.get() 在循环内**: `ray.get()` 应尽量延后，不要在循环里逐个 get，应批量提交后统一 `ray.get([refs])`
   ```python
   # 错误写法
   results = [ray.get(f.remote(x)) for x in data]
   # 正确写法
   results = ray.get([f.remote(x) for x in data])
   ```

2. **大对象重复传参**: 同一个大数组/DataFrame 被重复传给多个 remote 调用，应先 `ray.put()` 再传引用
   ```python
   # 错误写法
   [f.remote(large_data) for _ in range(100)]
   # 正确写法
   ref = ray.put(large_data)
   [f.remote(ref) for _ in range(100)]
   ```

3. **闭包捕获大对象**: remote 函数内引用了外部大变量，会被序列化到每个 worker

4. **remote 函数返回 ray.put()**: 不要在 remote 函数里 return ray.put(x)，直接 return x 即可

5. **全局可变状态**: 依赖全局变量在任务间共享数据 — 每个 worker 有独立副本，全局变量不共享。需要共享状态请用 Actor

6. **任务粒度过细**: 每个任务计算量太小（微秒级），调度开销大于计算。建议每个任务至少毫秒级

7. **working_dir 过大**: 提交目录超过 500MB（含 .git 等隐藏目录），应清理或用 .gitignore 排除

8. **缺少结果持久化**: 没有调用 `save_result()` 保存结果到 MinIO，任务完成后无法拿回结果

9. **缺少依赖声明**: 脚本 import 了第三方包但没通过 `--pip` 声明

10. **numpy 只读陷阱**: 从 object store 取出的 numpy 数组是只读的，如有修改操作需先 `.copy()`

11. **缺少 job 命名**: 建议通过 `--metadata` 加自定义名称，方便在 Dashboard 区分不同任务

### 第三步：输出验证报告

格式如下：

```
## Ray 适配性检查

**脚本**: <脚本路径>

### 阻断项
- [x] 通过: 使用了 @ray.remote 并行
- [x] 通过: 无交互式输入
- [ ] 未通过: 使用了 plt.show()，应改为 plt.savefig()
...

### 建议项
- [!] ray.get() 在循环内 (第 35 行)，建议批量获取
- [ok] 无大对象重复传参
...

### 结论
<适合提交 / 需要修改后提交 / 不适合 Ray 集群>
```

### 第四步：处理问题

- 如果有**阻断项**未通过：告知用户具体问题和修复方案。用户确认后帮用户修改代码，修改完成后重新验证。
- 如果只有**建议项**：列出建议，询问用户是否要优化后再提交，还是直接提交。

### 第五步：提交任务

验证通过后，帮用户构建提交命令。使用项目中的 `skills/ray_job.py` 工具：

```bash
# 基本提交
python skills/ray_job.py <script.py> --wait

# 带依赖
python skills/ray_job.py <script.py> --pip <packages> --wait

# 带自定义镜像（依赖预装，秒启动）
python skills/ray_job.py <script.py> --image <image_name> --wait

# 提交后不等待（后台运行）
python skills/ray_job.py <script.py>
```

分析脚本的 import 语句，自动识别需要的第三方包（排除标准库和 ray 自带的），构建 `--pip` 参数。如果使用了 `save_result()`，确保 `minio` 在依赖列表中。

**提交时不要用 `--wait`**，用不带 `--wait` 的方式提交（后台运行），这样才能继续做进度检查。

### 第六步：进度确认

提交成功拿到 Job ID 后，**等待约 30 秒**让任务启动，然后运行进度检查：

```bash
sleep 30 && python skills/progress_check.py <job_id>
```

根据返回的 JSON 数据，向用户报告：

1. **任务状态**: PENDING（排队中）/ RUNNING（运行中）/ SUCCEEDED / FAILED
2. **CPU 占用**: "你的任务正在使用 X 个 CPU（集群共 248 个）"
3. **已运行时长**: 从提交到现在
4. **预估剩余时间**（如果能推断）:
   - 如果日志中有进度信息（如 "50/1000 trials"），按比例估算
   - 如果是 `@ray.remote` 批量任务，根据 `总任务数 / 并发数 / 单任务耗时` 估算
   - 如果无法估算，说明"暂无法预估，建议稍后用 `/ray-status` 查看"
5. **最近日志**: 展示最后几行日志让用户了解运行情况

如果 30 秒后任务还是 PENDING，再等 30 秒检查一次。最多检查 2 次。

### 第七步：提交后指引

进度确认完成后，**必须主动告知**用户：

```
任务正在集群上运行！

  Job ID: <job_id>
  状态:   RUNNING
  CPU:    <N> 核运行中（集群共 248 核）
  已运行: <duration>
  预估:   <估算或"稍后用 /ray-status 查看">

  任务不依赖你的电脑。你可以关机、断开连接、关闭终端——任务不受影响。
  Job ID 已保存到本地，下次打开随时可查。

  回来后查看结果:
    /ray-status              — 或 —
    python skills/ray_job.py --result <job_id>
```

**这段提示必须完整输出，不要省略"可以关机"和 CPU 占用信息。**

### 第七步：依赖过多时建议镜像

如果 `--pip` 参数中包含 3 个以上的包，提醒用户：

```
提示：你有较多依赖，每次提交都要在线安装会很慢。
建议用 /ray-image 预构建自定义镜像，之后提交秒启动：
  /ray-image --name <环境名> --pip "<包列表>"
  然后提交时用 --image <环境名>
```

## Ray 2.54.0 关键 API 参考

### @ray.remote 参数
- `num_cpus=1`: CPU 需求（默认 1）
- `num_gpus=0`: GPU 需求（我们集群无 GPU）
- `memory`: 内存需求（bytes）
- `max_retries=3`: 失败重试次数
- `retry_exceptions=False`: 是否重试异常
- `num_returns=1`: 返回值数量
- `scheduling_strategy`: "DEFAULT"(bin-packing) / "SPREAD"(分散)

### 常用 API
- `ray.init()`: 连接集群（不带参数！）
- `func.remote(*args)`: 异步调用，返回 ObjectRef
- `ray.get(refs)`: 阻塞获取结果
- `ray.put(obj)`: 存入对象存储，返回 ObjectRef
- `ray.wait(refs, num_returns=1)`: 非阻塞等待

### runtime_env 选项
- `pip`: 依赖包列表
- `env_vars`: 环境变量
- `working_dir`: 工作目录（上限 500MB）
- `py_modules`: 额外 Python 模块
- `container`: Docker 容器镜像

## 注意事项

- 集群 Python 版本是 3.10，不要用 3.11+ 语法特性
- numpy 数组从 object store 取出是只读的，修改前需 `.copy()`
- Ray 资源调度是逻辑的，不强制物理限制，任务可能超出声明的资源导致 OOM
- 每个 worker 节点端口范围 10002-10300（298 个并发 worker）
- `save_result()` 函数定义在 `skills/template_task.py` 中，新任务可以直接复制使用
