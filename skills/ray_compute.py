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
PLACEHOLDER_VALUES = {"your_access_key", "your_secret_key", "your_ray_head_ip", "your_minio_host", ""}


def check_config():
    """前置配置检查，缺失时返回清晰的错误"""
    ray_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(ray_dir, ".env")

    if not os.path.exists(env_path):
        error("CONFIG_MISSING",
              f".env 文件不存在: {env_path}",
              f"运行 cd {ray_dir} && ./setup 配置集群连接信息")

    missing = []
    if not RAY_ADDRESS or "your_" in (RAY_ADDRESS or ""):
        missing.append("RAY_DASHBOARD_URL（Ray Dashboard 地址）")
    if not MINIO_ENDPOINT or MINIO_ENDPOINT in PLACEHOLDER_VALUES:
        missing.append("MINIO_ENDPOINT（MinIO API 地址）")
    if not MINIO_ACCESS_KEY or MINIO_ACCESS_KEY in PLACEHOLDER_VALUES:
        missing.append("MINIO_ACCESS_KEY（MinIO 访问密钥）")
    if not MINIO_SECRET_KEY or MINIO_SECRET_KEY in PLACEHOLDER_VALUES:
        missing.append("MINIO_SECRET_KEY（MinIO 私密密钥）")

    if missing:
        error("CONFIG_INCOMPLETE",
              f"以下配置未填写或为占位符: {', '.join(missing)}",
              f"运行 cd {ray_dir} && ./setup 交互式配置，或手动编辑 {env_path}")


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


def api_request(path, method="GET", data=None):
    """调 Ray Dashboard REST API（纯标准库，不需要 ray CLI）"""
    import urllib.request
    url = f"{RAY_ADDRESS}{path}"
    if data is not None:
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"}, method=method)
    else:
        req = urllib.request.Request(url, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def submit_job(script_path, pip_packages=None):
    """通过 REST API 提交任务（不需要 ray CLI）"""
    import urllib.request
    import zipfile
    import io
    import base64

    abs_script = os.path.abspath(script_path)
    working_dir = os.path.dirname(abs_script) or "."
    entrypoint = f"python {os.path.relpath(abs_script, working_dir)}"

    runtime_env = {
        "env_vars": {
            "MINIO_ENDPOINT": MINIO_ENDPOINT,
            "MINIO_ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO_SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO_BUCKET": MINIO_BUCKET,
        },
        "working_dir": working_dir,
    }
    if pip_packages:
        runtime_env["pip"] = pip_packages

    payload = {
        "entrypoint": entrypoint,
        "runtime_env": runtime_env,
        "entrypoint_num_cpus": 0,
    }

    url = f"{RAY_ADDRESS}/api/jobs/"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read().decode())
        return result.get("job_id") or result.get("submission_id"), None
    except Exception as e:
        return None, str(e)


def get_job_status(job_id):
    """通过 REST API 查询状态"""
    result = api_request(f"/api/jobs/{job_id}")
    if "error" in result:
        return "unknown"
    status = result.get("status", "").upper()
    if "SUCCEEDED" in status:
        return "succeeded"
    elif "FAILED" in status:
        return "failed"
    elif "RUNNING" in status:
        return "running"
    elif "PENDING" in status:
        return "pending"
    elif "STOPPED" in status:
        return "stopped"
    return "unknown"


def get_job_logs(job_id):
    """通过 REST API 获取日志"""
    result = api_request(f"/api/jobs/{job_id}/logs")
    if "error" in result:
        return ""
    return result.get("logs", "")


def fetch_result(job_id):
    """从 MinIO 下载结果到本地 ray-result/<job_id>/，返回结果数据和本地路径。
    优先用 minio 包，fallback 到纯 HTTP（零依赖）。"""
    import urllib.request

    ray_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_dir = os.path.join(ray_dir, "ray-result", job_id)
    os.makedirs(local_dir, exist_ok=True)

    prefix = f"jobs/{job_id}/"

    # 方法 1: urllib + MinIO presigned-like URL（公开 bucket）
    common_files = ["result.json", "summary.json"]
    results = {}
    local_files = []
    for filename in common_files:
        url = f"http://{MINIO_ENDPOINT}/{MINIO_BUCKET}/{prefix}{filename}"
        local_path = os.path.join(local_dir, filename)
        try:
            urllib.request.urlretrieve(url, local_path)
            if os.path.getsize(local_path) > 0:
                local_files.append(local_path)
                if filename.endswith(".json"):
                    with open(local_path) as f:
                        results[filename] = json.load(f)
            else:
                os.unlink(local_path)
        except Exception:
            if os.path.exists(local_path):
                os.unlink(local_path)

    if results:
        return results, local_files

    # Fallback 2: curl（支持 Basic Auth 访问私有 bucket）
    import shutil
    if shutil.which("curl"):
        for filename in common_files:
            url = f"http://{MINIO_ENDPOINT}/{MINIO_BUCKET}/{prefix}{filename}"
            local_path = os.path.join(local_dir, filename)
            try:
                ret = subprocess.run(
                    ["curl", "-sf", "-u", f"{MINIO_ACCESS_KEY}:{MINIO_SECRET_KEY}",
                     "-o", local_path, url],
                    capture_output=True, timeout=30
                )
                if ret.returncode == 0 and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    local_files.append(local_path)
                    if filename.endswith(".json"):
                        with open(local_path) as f:
                            results[filename] = json.load(f)
                else:
                    if os.path.exists(local_path):
                        os.unlink(local_path)
            except Exception:
                if os.path.exists(local_path):
                    os.unlink(local_path)

    if not results:
        return None, []
    return results, local_files


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
    check_config()
    if not os.path.exists(args.script):
        error("FILE_NOT_FOUND", f"脚本不存在: {args.script}")

    validate_script(args.script)

    pip = args.pip.split(",") if args.pip else None
    job_id, err = submit_job(args.script, pip)
    if err:
        error("SUBMIT_FAILED", err, "检查集群是否在线: python skills/check_env.py")
    if not job_id:
        error("NO_JOB_ID", "无法获取 Job ID")

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
            result_data, local_files = fetch_result(job_id)
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

            # 本地文件路径（AI 可用 Read 工具直接读取）
            if local_files:
                out["local_files"] = local_files

            if result_data:
                result_str = json.dumps(result_data, default=str)
                if len(result_str) > RESULT_SIZE_THRESHOLD:
                    out["result_summary"] = {k: type(v).__name__ for k, v in result_data.items()}
                    out["hint"] = f"结果较大，已保存到本地。用 Read 工具读取: {local_files[0] if local_files else ''}"
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
    check_config()
    if not os.path.exists(args.script):
        error("FILE_NOT_FOUND", f"脚本不存在: {args.script}")

    validate_script(args.script)

    pip = args.pip.split(",") if args.pip else None
    job_id, err = submit_job(args.script, pip)
    if err:
        error("SUBMIT_FAILED", err)
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
        result_data, local_files = fetch_result(args.job_id)
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
    check_config()
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
        job_id, err = submit_job(tmp_path, pip)
        if err:
            os.unlink(tmp_path)
            error("SUBMIT_FAILED", err)
        if not job_id:
            os.unlink(tmp_path)
            error("NO_JOB_ID", "无法获取 Job ID")

        # 同步等待
        timeout = args.timeout or 300
        start = time.time()
        while time.time() - start < timeout:
            status = get_job_status(job_id)
            if status == "succeeded":
                logs = get_job_logs(job_id)
                result_data, local_files = fetch_result(job_id)

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
