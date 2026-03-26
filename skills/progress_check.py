"""
任务进度检查 — 查询 Ray Dashboard API 获取任务运行状态

用法:
    python skills/progress_check.py <job_id>

输出: JSON 格式的进度信息
"""
import json
import os
import sys
import urllib.request
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.config import RAY_ADDRESS


def get_job_info(job_id):
    """从 Ray Jobs API 获取任务信息"""
    try:
        url = f"{RAY_ADDRESS}/api/jobs/{job_id}"
        resp = urllib.request.urlopen(url, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def get_cluster_status():
    """获取集群资源使用情况"""
    try:
        url = f"{RAY_ADDRESS}/api/cluster_status"
        resp = urllib.request.urlopen(url, timeout=10)
        return json.loads(resp.read().decode())
    except Exception:
        return None


def get_job_logs_tail(job_id, lines=30):
    """获取任务最近的日志"""
    try:
        url = f"{RAY_ADDRESS}/api/jobs/{job_id}/logs"
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read().decode())
        log_text = data.get("logs", "")
        log_lines = log_text.strip().split("\n")
        return log_lines[-lines:] if len(log_lines) > lines else log_lines
    except Exception:
        return []


def analyze_progress(job_id):
    """综合分析任务进度"""
    result = {
        "job_id": job_id,
        "status": "UNKNOWN",
        "cpu_used": 0,
        "cpu_total": 0,
        "duration_seconds": 0,
        "progress_hint": "",
        "log_tail": [],
    }

    # 1. 任务状态
    job = get_job_info(job_id)
    if "error" in job:
        result["status"] = "ERROR"
        result["progress_hint"] = job["error"]
        return result

    result["status"] = job.get("status", "UNKNOWN")
    result["entrypoint"] = job.get("entrypoint", "")

    # 计算运行时长
    start_ms = job.get("start_time")
    if start_ms:
        result["duration_seconds"] = int((time.time() * 1000 - start_ms) / 1000)

    # 2. 集群资源
    cluster = get_cluster_status()
    if cluster:
        # 解析已用/总计 CPU
        try:
            auto_scaling = cluster.get("autoscalingStatus", "")
            # 尝试从 cluster_status 获取资源信息
            usage = cluster.get("clusterStatus", {})
            if usage:
                load = usage.get("loadMetricsReport", {})
                used = load.get("usedResources", {})
                total = load.get("totalResources", {})
                result["cpu_used"] = used.get("CPU", 0)
                result["cpu_total"] = total.get("CPU", 0)
                result["memory_used_gb"] = round(used.get("memory", 0) / 1e9, 1)
                result["memory_total_gb"] = round(total.get("memory", 0) / 1e9, 1)
        except Exception:
            pass

    # 3. 日志尾部（可能包含进度信息）
    if result["status"] == "RUNNING":
        result["log_tail"] = get_job_logs_tail(job_id, 20)

        # 尝试从日志推断进度
        for line in reversed(result["log_tail"]):
            # Ray Tune 风格进度: "Trial status: 100 TERMINATED | 50 RUNNING"
            if "Trial status" in line or "trial" in line.lower():
                result["progress_hint"] = line.strip()
                break
            # 百分比进度
            if "%" in line:
                result["progress_hint"] = line.strip()
                break
            # 自定义进度: "Progress: 50/100" 或 "Completed 50 of 100"
            if "progress" in line.lower() or "completed" in line.lower():
                result["progress_hint"] = line.strip()
                break

    return result


def format_output(progress):
    """格式化输出"""
    status = progress["status"]
    job_id = progress["job_id"]
    duration = progress.get("duration_seconds", 0)
    cpu_used = progress.get("cpu_used", 0)
    cpu_total = progress.get("cpu_total", 0)
    mem_used = progress.get("memory_used_gb", 0)
    mem_total = progress.get("memory_total_gb", 0)

    # 时长格式化
    if duration > 3600:
        dur_str = f"{duration // 3600}h {(duration % 3600) // 60}m"
    elif duration > 60:
        dur_str = f"{duration // 60}m {duration % 60}s"
    else:
        dur_str = f"{duration}s"

    print(f"Job: {job_id}")
    print(f"状态: {status}")

    if status == "RUNNING":
        print(f"已运行: {dur_str}")
        if cpu_total > 0:
            print(f"CPU: {cpu_used:.0f} / {cpu_total:.0f} ({cpu_used/cpu_total*100:.0f}% 使用中)")
        if mem_total > 0:
            print(f"内存: {mem_used}GB / {mem_total}GB")
        if progress.get("progress_hint"):
            print(f"进度: {progress['progress_hint']}")
    elif status == "SUCCEEDED":
        print(f"总耗时: {dur_str}")
        print(f"结果拉取: python skills/ray_job.py --result {job_id}")
    elif status == "FAILED":
        print(f"运行了: {dur_str}")
        print(f"查看日志: python skills/ray_job.py --logs {job_id}")
    elif status == "PENDING":
        print("任务排队中，等待资源分配...")

    # 输出日志尾部
    if progress.get("log_tail") and status == "RUNNING":
        print(f"\n最近日志:")
        for line in progress["log_tail"][-5:]:
            if line.strip():
                print(f"  {line.strip()}")

    # 输出 JSON 供 AI 解析
    print(f"\n---JSON---")
    print(json.dumps(progress, ensure_ascii=False, default=str))


def main():
    if len(sys.argv) < 2:
        print("用法: python skills/progress_check.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    progress = analyze_progress(job_id)
    format_output(progress)


if __name__ == "__main__":
    main()
