"""
Ray 集群透明计算层 — AI 在对话中直接调用，不中断推理循环

用法:
    # 同步执行（短任务，< 10 分钟）
    python skills/ray_compute.py run tasks/my_task.py [--pip pkg1,pkg2] [--timeout 300]

    # 异步提交（长任务）
    python skills/ray_compute.py submit tasks/my_task.py [--pip pkg1,pkg2]

    # 查询异步任务结果
    python skills/ray_compute.py result <job_id>

    # 从 stdin 执行（AI 生成代码通过管道传入）
    python skills/ray_compute.py exec [--pip pkg1,pkg2] <<'PYEOF'
    import ray
    ...
    PYEOF

所有输出都是 JSON，AI 可直接解析。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.config import RAY_ADDRESS, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET

RESULT_SIZE_THRESHOLD = 1024 * 1024  # 1MB，超过存 MinIO


def output(data):
    """统一 JSON 输出"""
    print(json.dumps(data, ensure_ascii=False, default=str))
    sys.exit(0 if data.get("status") != "error" else 1)


def error(code, message, suggestion="", traceback_str=""):
    """错误输出"""
    output({
        "status": "error",
        "code": code,
        "message": message,
        "suggestion": suggestion,
        "traceback": traceback_str,
    })


def build_submit_cmd(script_path, pip_packages=None):
    """构建 ray job submit 命令"""
    abs_script = os.path.abspath(script_path)
    working_dir = os.path.dirname(abs_script) or "."
    entrypoint = os.path.relpath(abs_script, working_dir)

    cmd = [
        "ray", "job", "submit",
        "--address", RAY_ADDRESS,
        "--working-dir", working_dir,
        "--no-wait",
    ]

    runtime_env = {
        "env_vars": {
            "MINIO_ENDPOINT": MINIO_ENDPOINT,
            "MINIO_ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO_SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO_BUCKET": MINIO_BUCKET,
        }
    }
    if pip_packages:
        runtime_env["pip"] = pip_packages

    cmd += ["--runtime-env-json", json.dumps(runtime_env)]
    cmd += ["--", "python", entrypoint]
    return cmd


def extract_job_id(stdout):
    """从 ray job submit 输出中提取 Job ID"""
    for line in stdout.splitlines():
        if "raysubmit_" in line:
            for word in line.split():
                w = word.strip("'\".,")
                if w.startswith("raysubmit_"):
                    return w
    return None


def get_job_status(job_id):
    """查询 job 状态"""
    env = os.environ.copy()
    env["RAY_ADDRESS"] = RAY_ADDRESS
    result = subprocess.run(
        ["ray", "job", "status", job_id],
        capture_output=True, text=True, env=env, timeout=30
    )
    status_text = result.stdout.strip()
    if "SUCCEEDED" in status_text:
        return "succeeded"
    elif "FAILED" in status_text:
        return "failed"
    elif "RUNNING" in status_text:
        return "running"
    elif "PENDING" in status_text:
        return "pending"
    elif "STOPPED" in status_text:
        return "stopped"
    return "unknown"


def get_job_logs(job_id):
    """获取 job 日志"""
    env = os.environ.copy()
    env["RAY_ADDRESS"] = RAY_ADDRESS
    result = subprocess.run(
        ["ray", "job", "logs", job_id],
        capture_output=True, text=True, env=env, timeout=60
    )
    return result.stdout


def fetch_result(job_id):
    """从 MinIO 下载结果"""
    try:
        from minio import Minio
    except ImportError:
        return None

    client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                   secret_key=MINIO_SECRET_KEY, secure=False)

    prefix = f"jobs/{job_id}/"
    objects = list(client.list_objects(MINIO_BUCKET, prefix=prefix, recursive=True))
    if not objects:
        return None

    results = {}
    for obj in objects:
        filename = obj.object_name.replace(prefix, "")
        resp = client.get_object(MINIO_BUCKET, obj.object_name)
        data = resp.read()

        if filename.endswith(".json"):
            try:
                results[filename] = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                results[filename] = data.decode("utf-8")
        else:
            # 大文件返回 URI
            results[filename] = f"minio://{MINIO_BUCKET}/{obj.object_name}"

    return results


def validate_script(script_path):
    """基本验证"""
    try:
        with open(script_path) as f:
            code = f.read()
        compile(code, script_path, "exec")
    except SyntaxError as e:
        error("SYNTAX_CHECK", f"语法错误: {e.msg} (行 {e.lineno})",
              f"修复第 {e.lineno} 行的语法错误")

    # 检查弃用导入
    if "from ray.air import" in code:
        error("DEPRECATED_IMPORT", "from ray.air import 已弃用",
              "改用 from ray.tune import RunConfig")

    if "from ray.train import RunConfig" in code:
        error("DEPRECATED_IMPORT", "from ray.train import RunConfig 已弃用",
              "改用 from ray.tune import RunConfig")


def cmd_run(args):
    """同步执行：提交 → 等 → 拿结果"""
    if not os.path.exists(args.script):
        error("FILE_NOT_FOUND", f"脚本不存在: {args.script}")

    validate_script(args.script)

    pip = args.pip.split(",") if args.pip else None
    cmd = build_submit_cmd(args.script, pip)

    env = os.environ.copy()
    env["RAY_ADDRESS"] = RAY_ADDRESS
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)

    if result.returncode != 0:
        error("SUBMIT_FAILED", result.stderr.strip(),
              "检查集群是否在线: python skills/check_env.py")

    job_id = extract_job_id(result.stdout)
    if not job_id:
        error("NO_JOB_ID", "无法提取 Job ID", result.stdout[:500])

    # 记录到本地历史
    history_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".jobs")
    os.makedirs(history_dir, exist_ok=True)
    from datetime import datetime
    record = json.dumps({
        "job_id": job_id,
        "script": os.path.basename(args.script),
        "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "sync",
    }, ensure_ascii=False)
    with open(os.path.join(history_dir, "history.jsonl"), "a") as f:
        f.write(record + "\n")

    # 轮询等待
    timeout = args.timeout or 300
    start = time.time()
    while time.time() - start < timeout:
        status = get_job_status(job_id)
        if status == "succeeded":
            # 拿结果
            result_data = fetch_result(job_id)
            logs = get_job_logs(job_id)

            # 尝试从日志最后提取 JSON 输出
            log_result = None
            for line in reversed(logs.strip().splitlines()):
                line = line.strip()
                if line.startswith("{") or line.startswith("["):
                    try:
                        log_result = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        pass

            duration = round(time.time() - start, 1)
            out = {
                "status": "success",
                "job_id": job_id,
                "duration": duration,
            }

            if result_data:
                # 检查大小
                result_str = json.dumps(result_data, default=str)
                if len(result_str) > RESULT_SIZE_THRESHOLD:
                    out["result_uri"] = f"ray-result/jobs/{job_id}/"
                    out["result_summary"] = {k: type(v).__name__ for k, v in result_data.items()}
                else:
                    out["result"] = result_data
            elif log_result:
                out["result"] = log_result
            else:
                out["result"] = {"logs_tail": logs.strip().splitlines()[-10:]}

            output(out)

        elif status == "failed":
            logs = get_job_logs(job_id)
            error("RUNTIME", f"任务失败 ({job_id})",
                  "检查代码逻辑或依赖是否完整",
                  logs[-2000:] if logs else "")

        elif status == "stopped":
            error("STOPPED", f"任务被停止 ({job_id})")

        time.sleep(5)

    # 超时
    error("TIMEOUT", f"任务超时 ({timeout}s)，仍在运行: {job_id}",
          f"用异步模式: python skills/ray_compute.py submit {args.script}")


def cmd_submit(args):
    """异步提交：立即返回 job_id"""
    if not os.path.exists(args.script):
        error("FILE_NOT_FOUND", f"脚本不存在: {args.script}")

    validate_script(args.script)

    pip = args.pip.split(",") if args.pip else None
    cmd = build_submit_cmd(args.script, pip)

    env = os.environ.copy()
    env["RAY_ADDRESS"] = RAY_ADDRESS
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)

    if result.returncode != 0:
        error("SUBMIT_FAILED", result.stderr.strip())

    job_id = extract_job_id(result.stdout)
    if not job_id:
        error("NO_JOB_ID", "无法提取 Job ID")

    output({
        "status": "submitted",
        "job_id": job_id,
        "message": "任务已提交，用 result 命令查询",
    })


def cmd_result(args):
    """查询异步任务结果"""
    status = get_job_status(args.job_id)

    if status == "succeeded":
        result_data = fetch_result(args.job_id)
        logs = get_job_logs(args.job_id)

        log_result = None
        for line in reversed(logs.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") or line.startswith("["):
                try:
                    log_result = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass

        out = {"status": "success", "job_id": args.job_id}
        if result_data:
            result_str = json.dumps(result_data, default=str)
            if len(result_str) > RESULT_SIZE_THRESHOLD:
                out["result_uri"] = f"ray-result/jobs/{args.job_id}/"
                out["result_summary"] = {k: type(v).__name__ for k, v in result_data.items()}
            else:
                out["result"] = result_data
        elif log_result:
            out["result"] = log_result
        else:
            out["result"] = {"logs_tail": logs.strip().splitlines()[-10:]}
        output(out)

    elif status == "failed":
        logs = get_job_logs(args.job_id)
        error("RUNTIME", f"任务失败", "", logs[-2000:] if logs else "")

    elif status in ("running", "pending"):
        # 尝试从日志获取进度
        logs = get_job_logs(args.job_id)
        progress = ""
        for line in reversed(logs.strip().splitlines()[-20:]):
            if "%" in line or "progress" in line.lower() or "trial" in line.lower():
                progress = line.strip()
                break
        output({
            "status": status,
            "job_id": args.job_id,
            "progress": progress,
        })

    else:
        output({"status": status, "job_id": args.job_id})


def cmd_exec(args):
    """从 stdin 读取代码执行"""
    code = sys.stdin.read()
    if not code.strip():
        error("EMPTY_CODE", "stdin 没有代码")

    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir="tasks",
                                      prefix="ray_exec_", delete=False) as f:
        f.write(code)
        tmp_path = f.name

    try:
        # 语法检查
        try:
            compile(code, tmp_path, "exec")
        except SyntaxError as e:
            os.unlink(tmp_path)
            error("SYNTAX_CHECK", f"语法错误: {e.msg} (行 {e.lineno})",
                  f"修复第 {e.lineno} 行")

        # 弃用检查
        if "from ray.air import" in code:
            os.unlink(tmp_path)
            error("DEPRECATED_IMPORT", "from ray.air import 已弃用",
                  "改用 from ray.tune import RunConfig")

        # 提交
        pip = args.pip.split(",") if args.pip else None
        cmd = build_submit_cmd(tmp_path, pip)

        env = os.environ.copy()
        env["RAY_ADDRESS"] = RAY_ADDRESS
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)

        if result.returncode != 0:
            os.unlink(tmp_path)
            error("SUBMIT_FAILED", result.stderr.strip())

        job_id = extract_job_id(result.stdout)
        if not job_id:
            os.unlink(tmp_path)
            error("NO_JOB_ID", "无法提取 Job ID")

        # 同步等待
        timeout = args.timeout or 300
        start = time.time()
        while time.time() - start < timeout:
            status = get_job_status(job_id)
            if status == "succeeded":
                logs = get_job_logs(job_id)
                result_data = fetch_result(job_id)

                log_result = None
                for line in reversed(logs.strip().splitlines()):
                    line = line.strip()
                    if line.startswith("{") or line.startswith("["):
                        try:
                            log_result = json.loads(line)
                            break
                        except json.JSONDecodeError:
                            pass

                out = {"status": "success", "job_id": job_id,
                       "duration": round(time.time() - start, 1)}
                if result_data:
                    out["result"] = result_data
                elif log_result:
                    out["result"] = log_result
                else:
                    out["result"] = {"logs_tail": logs.strip().splitlines()[-10:]}

                os.unlink(tmp_path)
                output(out)

            elif status == "failed":
                logs = get_job_logs(job_id)
                os.unlink(tmp_path)
                error("RUNTIME", "任务失败", "", logs[-2000:] if logs else "")

            time.sleep(5)

        os.unlink(tmp_path)
        error("TIMEOUT", f"超时 ({timeout}s)，job_id: {job_id}",
              f"用 result 命令查询: python skills/ray_compute.py result {job_id}")

    except SystemExit:
        raise
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        error("INTERNAL", str(e))


def main():
    parser = argparse.ArgumentParser(description="Ray 集群透明计算层")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="同步执行")
    p_run.add_argument("script", help="Python 脚本路径")
    p_run.add_argument("--pip", help="pip 依赖，逗号分隔")
    p_run.add_argument("--timeout", type=int, default=300, help="超时秒数（默认 300）")

    p_submit = sub.add_parser("submit", help="异步提交")
    p_submit.add_argument("script", help="Python 脚本路径")
    p_submit.add_argument("--pip", help="pip 依赖，逗号分隔")

    p_result = sub.add_parser("result", help="查询结果")
    p_result.add_argument("job_id", help="Job ID")

    p_exec = sub.add_parser("exec", help="从 stdin 执行代码")
    p_exec.add_argument("--pip", help="pip 依赖，逗号分隔")
    p_exec.add_argument("--timeout", type=int, default=300, help="超时秒数")

    args = parser.parse_args()

    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "submit":
        cmd_submit(args)
    elif args.cmd == "result":
        cmd_result(args)
    elif args.cmd == "exec":
        cmd_exec(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
