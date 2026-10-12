"""依赖引导（2026-10-11 收尾）。

Nekro 的插件包没有清单文件用来声明依赖，因此这里做"缺失即动态安装"：

1. 先直接 import；
2. 失败时调用 Nekro SDK 的 `dynamic_import_pkg`（走
   `DYNAMIC_PLUGIN_INSTALL_MIRROR` 与代理配置）；
3. 仍失败则抛出可读错误，指明需要装什么。

依赖已存在时（容器里的 Pillow、本地测试环境）不会触发任何安装动作，
因此在纯单测与隔离 smoke 中同样是安全的。
"""

from __future__ import annotations

import importlib
from typing import Any, List, Tuple

# (import 名, pip 规范)
REQUIREMENTS: Tuple[Tuple[str, str], ...] = (
    ("PIL", "Pillow>=12.2,<13"),
    ("mcstatus", "mcstatus>=14.2,<15"),
    ("aiohttp", "aiohttp>=3.13,<4"),
    ("tzdata", "tzdata>=2025.3"),
)

_errors: List[str] = []


def verify_imports() -> None:
    """确认依赖可导入；失败时抛 ImportError（附安装指引）。"""
    _errors.clear()
    for name, spec in REQUIREMENTS:
        _ensure(name, spec)


def _ensure(import_name: str, spec: str) -> Any:
    try:
        return importlib.import_module(import_name)
    except ImportError as exc:
        _errors.append(f"{import_name} 未安装（{exc}）")

    try:
        from nekro_agent.api.plugin import dynamic_import_pkg  # type: ignore
    except Exception as exc:  # noqa: BLE001
        _errors.append(f"{import_name} 无法动态安装：{type(exc).__name__}")
        raise ImportError(_hint()) from exc

    try:
        return dynamic_import_pkg(spec, import_name=import_name)
    except Exception as exc:  # noqa: BLE001
        _errors.append(f"{import_name} 动态安装失败：{exc}")
        raise ImportError(_hint()) from exc


def _hint() -> str:
    detail = "；".join(_errors) or "未知原因"
    need = " ".join(f"'{spec}'" for _, spec in REQUIREMENTS)
    return (
        f"nekro_mc_status 运行依赖不可用：{detail}。"
        f"请在 Nekro 容器内安装：/app/.venv/bin/python -m pip install {need}，"
        "或确认插件动态安装镜像/代理可用。"
    )
