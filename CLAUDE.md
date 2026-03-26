# Ray 集群任务工具

本项目提供一套 Claude Code skill，帮助用户（包括零 Ray 经验的用户）在 248 CPU 的 Ray 集群上提交和管理分布式计算任务。

## 可用 Skill

| Skill | 功能 | 适用场景 |
|-------|------|---------|
| `/ray-new` | 生成 Ray 任务脚本 | "我想并行化一个 Python 函数"、"帮我写个调参任务" |
| `/ray-push` | 验证 + 提交任务到集群 | "提交这个脚本到集群" |
| `/ray-status` | 集群和任务监控 | "任务完成了吗"、"集群状态"、"拿结果" |

## 集群信息

- Ray 2.54.0, Python 3.12
- 无 GPU
- MinIO 对象存储用于结果持久化
- 集群地址和密钥配置在 `.env` 文件中

## 快速开始

```bash
# 1. 配置环境
cp .env.example .env
# 编辑 .env 填入 MinIO 密钥（找集群管理员获取）

# 2. 安装依赖
pip install 'ray[default]' minio

# 3. 检查环境
python skills/check_env.py
```

## 项目结构

```
.claude/commands/     # Claude Code skill 定义
  ray-new.md          # 生成任务脚本
  ray-push.md         # 验证 + 提交
  ray-status.md       # 监控 + 结果
skills/               # Python 工具
  ray_job.py          # 任务提交 CLI
  minio_io.py         # MinIO 读写
  build_image.py      # Docker 镜像构建
  template_task.py    # 任务模板（含 save_result）
  config.py           # 配置加载
  check_env.py        # 环境检查
tasks/                # 用户任务脚本
```

## 注意事项

- 集群 Python 3.12，不要用 3.13+ 语法
- `ray.init()` 不带参数（由 Jobs API 自动注入地址）
- 用 `save_result()` 保存结果到 MinIO，否则任务完成后拿不到结果
- 大对象先 `ray.put()` 再传引用，避免重复序列化
