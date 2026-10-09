#!/usr/bin/env python3
"""测试脚本公共方法。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
PYTHON = sys.executable
# DEFAULT_BASE_URL = "http://tracker-poc.pgw.metax-tech.com"
DEFAULT_BASE_URL = "http://10.2.201.98:8080"


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Jira 基础地址",
    )
    parser.add_argument(
        "--access-token",
        required=True,
        help="Jira Personal Access Token",
    )


def run_script(script_name: str, args: list[str]) -> int:
    if os.environ.get("JIRA_INTEGRATION_TESTS") != "explicitly-approved":
        raise RuntimeError("这是会访问真实 Jira 的集成测试；默认禁用。请使用隔离测试项目并明确授权。")
    script_path = SCRIPTS_DIR / script_name
    command = [PYTHON, str(script_path), *args]
    print("执行已明确授权的集成测试:", script_name, "（凭据参数不输出）")
    completed = subprocess.run(command, cwd=str(SKILL_DIR))
    return completed.returncode
