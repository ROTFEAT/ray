"""
构建自定义 Ray 镜像并推送到内网 Registry

用法:
    # 从 requirements.txt 构建
    python skills/build_image.py --name my-env --req requirements.txt

    # 直接指定包
    python skills/build_image.py --name ml-env --pip "optuna scikit-learn xgboost pandas"

    # 列出已有镜像
    python skills/build_image.py --list

    # 删除镜像
    python skills/build_image.py --delete my-env

构建完成后提交任务:
    python skills/ray_job.py tasks/my_task.py --image my-env --wait
"""
import argparse
import subprocess
import sys
import os
import tempfile
import json
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.config import get

REGISTRY = get("REGISTRY_URL", "")
BASE_IMAGE = "rayproject/ray:2.54.0-py312"


def build_and_push(name, pip_packages=None, requirements_file=None):
    """构建自定义镜像并推送"""
    tag = f"{REGISTRY}/ray-{name}:latest"

    with tempfile.TemporaryDirectory() as tmpdir:
        # 生成 Dockerfile
        dockerfile = f"FROM {BASE_IMAGE}\n"

        if requirements_file:
            # 复制 requirements.txt 进去
            import shutil
            shutil.copy(requirements_file, os.path.join(tmpdir, "requirements.txt"))
            dockerfile += "COPY requirements.txt /tmp/requirements.txt\n"
            dockerfile += "RUN pip install --no-cache-dir -r /tmp/requirements.txt\n"
        elif pip_packages:
            dockerfile += f"RUN pip install --no-cache-dir {pip_packages}\n"

        dockerfile_path = os.path.join(tmpdir, "Dockerfile")
        with open(dockerfile_path, "w") as f:
            f.write(dockerfile)

        print(f"构建镜像: {tag}")
        print(f"基础镜像: {BASE_IMAGE}")
        print(f"Dockerfile:\n{dockerfile}")

        # Build
        ret = subprocess.run(
            ["docker", "build", "-t", tag, tmpdir],
            capture_output=False
        )
        if ret.returncode != 0:
            print("构建失败")
            return

        # Push
        print(f"\n推送到 {REGISTRY}...")
        ret = subprocess.run(
            ["docker", "push", tag],
            capture_output=False
        )
        if ret.returncode != 0:
            print("推送失败。确保 Docker 配置了 insecure-registries:")
            print(f'  /etc/docker/daemon.json: {{"insecure-registries": ["{REGISTRY}"]}}')
            return

        print(f"\n镜像已就绪: {tag}")
        print(f"使用方式: python skills/ray_job.py tasks/xxx.py --image {name} --wait")


def list_images():
    """列出 Registry 中的镜像"""
    try:
        url = f"http://{REGISTRY}/v2/_catalog"
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        repos = data.get("repositories", [])
        if not repos:
            print("Registry 中没有镜像")
            return
        print(f"{'镜像名':<30} {'Tags'}")
        print("-" * 50)
        for repo in repos:
            tag_url = f"http://{REGISTRY}/v2/{repo}/tags/list"
            tag_resp = urllib.request.urlopen(tag_url, timeout=5)
            tag_data = json.loads(tag_resp.read())
            tags = ", ".join(tag_data.get("tags", []))
            print(f"{repo:<30} {tags}")
    except Exception as e:
        print(f"无法连接 Registry: {e}")


def delete_image(name):
    """删除 Registry 中的镜像"""
    repo = f"ray-{name}"
    try:
        # 获取 digest
        url = f"http://{REGISTRY}/v2/{repo}/manifests/latest"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.docker.distribution.manifest.v2+json"
        })
        resp = urllib.request.urlopen(req, timeout=5)
        digest = resp.headers.get("Docker-Content-Digest")
        if not digest:
            print("无法获取 digest")
            return

        # 删除
        del_url = f"http://{REGISTRY}/v2/{repo}/manifests/{digest}"
        req = urllib.request.Request(del_url, method="DELETE")
        urllib.request.urlopen(req, timeout=5)
        print(f"已删除: {repo}")
    except Exception as e:
        print(f"删除失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="构建自定义 Ray 镜像")
    parser.add_argument("--name", help="镜像名称（如 ml-env）")
    parser.add_argument("--pip", help="pip 包列表，空格分隔")
    parser.add_argument("--req", help="requirements.txt 文件路径")
    parser.add_argument("--list", action="store_true", help="列出已有镜像")
    parser.add_argument("--delete", metavar="NAME", help="删除镜像")

    args = parser.parse_args()

    if args.list:
        list_images()
    elif args.delete:
        delete_image(args.delete)
    elif args.name:
        if not args.pip and not args.req:
            print("需要指定 --pip 或 --req")
            return
        build_and_push(args.name, args.pip, args.req)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
