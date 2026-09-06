"""插件仓测试入口：以独立 pytest 会话运行仓库门禁与 V3 回归。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent


def _has_tests(path: Path) -> bool:
    """判断目标目录是否包含 pytest 用例。"""
    return path.is_dir() and any(path.rglob("test_*.py"))


def _run_target(path: Path, extra_args: list[str]) -> int:
    """在独立进程中运行一个测试分组，避免宿主模块状态跨组泄漏。"""
    return subprocess.call(
        [sys.executable, "-m", "pytest", str(path), *extra_args],
        cwd=str(REPO_ROOT),
    )


def main(argv: list[str] | None = None) -> int:
    """依次运行仓库工具测试和当前 V3 插件测试。"""
    extra_args = list(sys.argv[1:] if argv is None else argv)
    exit_code = 0
    for target in (TESTS_DIR / "ci", TESTS_DIR / "v3"):
        if not _has_tests(target):
            continue
        return_code = _run_target(target, extra_args)
        exit_code = exit_code or return_code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
