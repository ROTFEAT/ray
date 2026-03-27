"""
安装 Ray 集群 hooks 到全局 ~/.claude/settings.json
合并而非覆盖，不会破坏已有配置
"""
import json
import os
import sys


def main():
    if len(sys.argv) < 3:
        print("用法: python install_hooks.py <settings.json路径> <ray项目路径>")
        sys.exit(1)

    settings_path = sys.argv[1]
    ray_dir = sys.argv[2]

    # 读取已有配置
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, IOError):
            settings = {}

    if "hooks" not in settings:
        settings["hooks"] = {}

    # ── UserPromptSubmit: 关键词触发提醒 ──
    user_hook = {
        "hooks": [{
            "type": "command",
            "command": (
                'jq -r \'.user_message // .prompt // ""\' | '
                "grep -qiE '优化|调参|回测|搜索|蒙特卡洛|并行|批量|参数扫描|网格搜索|遍历|寻参|暴力|穷举|模拟|仿真|训练|推理|预测|交叉验证|特征工程|清洗|ETL|压测|基准测试|太慢|跑不动|算力|算一下|上百|上千|上万|百万|千万|亿|集群|加速|backtest|optimize|sweep|grid.search|monte.carlo|hyperparameter|brute.force|tune|benchmark|simulate|train|inference|predict|batch|parallel|distributed|cluster|compute|bootstrap|walk.forward|optuna|scipy.optimize|differential.evolution|genetic|anneal|ray.tune|trial|experiment|epoch' "
                "&& echo '{\"hookSpecificOutput\":{\"hookEventName\":\"UserPromptSubmit\","
                f"\"additionalContext\":\"提醒: 你有一个 248 CPU 的 Ray 集群可用。需要大量计算时用 python {ray_dir}/skills/ray_compute.py exec 提交到集群，不要在本地跑。详见 {ray_dir}/CLAUDE.md。\"}}' || true"
            )
        }]
    }

    # ── PostToolUse: 用过集群后持续提醒 ──
    post_hook = {
        "matcher": "Bash",
        "hooks": [{
            "type": "command",
            "command": (
                'jq -r \'.tool_input.command // ""\' | '
                "grep -q 'ray_compute.py' "
                "&& echo '{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\","
                "\"additionalContext\":\"你刚才用了 Ray 集群计算。后续计算密集型步骤继续用 ray_compute.py 提交到集群。不要切回本地。拿到结果后分析，不满意就调参数再跑。\"}}' || true"
            )
        }]
    }

    # 合并 UserPromptSubmit（不重复添加）
    if "UserPromptSubmit" not in settings["hooks"]:
        settings["hooks"]["UserPromptSubmit"] = []

    # 移除旧的 ray hook（如果有），再添加新的——这样更新时内容也会刷新
    settings["hooks"]["UserPromptSubmit"] = [
        h for h in settings["hooks"]["UserPromptSubmit"]
        if "ray_compute.py" not in json.dumps(h)
    ]
    settings["hooks"]["UserPromptSubmit"].append(user_hook)

    # PostToolUse 同理
    if "PostToolUse" not in settings["hooks"]:
        settings["hooks"]["PostToolUse"] = []

    settings["hooks"]["PostToolUse"] = [
        h for h in settings["hooks"]["PostToolUse"]
        if "ray_compute.py" not in json.dumps(h)
    ]
    settings["hooks"]["PostToolUse"].append(post_hook)

    # 写回
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
