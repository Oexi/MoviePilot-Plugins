# _*_ coding: utf-8 _*_
"""Deterministic UI dictionaries for ProwlarrExtend.

The plugin entry point supplies already-loaded indexer options and snapshots;
this module only builds immutable-shaped form/page dictionaries.  It has no
MoviePilot, network, logging, or plugin-state dependencies.
"""

import copy
from collections.abc import Mapping
from typing import Any, Dict, List, Tuple

from ._indexers import privacy_label


def build_form(
        site_options: object,
        timeout_default: int = 30,
        timeout_min: int = 5,
        timeout_max: int = 120,
) -> Tuple[List[dict], Dict[str, Any]]:
    """Build Prowlarr configuration form and defaults.

    Configuration is intentionally limited to the current V3 contract:
    enabled/proxy/host/api_key/cron/timeout and the indexer whitelist.  In
    particular, Prowlarr's API does not need a web-login credential field.
    """
    options = copy.deepcopy(site_options) if isinstance(site_options, list) else []
    timeout_default_text = str(timeout_default)
    return [
        {
            "component": "VForm",
            "content": [
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "enabled",
                                        "label": "启用插件",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "timeout",
                                        "label": "搜索超时（秒）",
                                        "placeholder": timeout_default_text,
                                        "hint": f"仅用于 Torznab 搜索，范围 {timeout_min}-{timeout_max} 秒，超出范围会自动限制",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [
                                {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "proxy",
                                        "label": "使用代理服务器",
                                    },
                                }
                            ],
                        },
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "host",
                                        "label": "Prowlarr地址",
                                        "placeholder": "http://127.0.0.1:9696",
                                        "hint": "Prowlarr访问地址和端口，如为 https 请加 https:// 前缀",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "api_key",
                                        "label": "Prowlarr API Key",
                                        "placeholder": "",
                                        "hint": "Prowlarr设置页面中的 API Key",
                                    },
                                }
                            ],
                        },
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VTextField",
                                    "props": {
                                        "model": "cron",
                                        "label": "更新周期",
                                        "placeholder": "0 0 * * *",
                                        "hint": "索引列表更新周期，支持 5 位 cron 表达式，默认每 24 小时运行一次",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VSelect",
                                    "props": {
                                        "model": "indexer_sites",
                                        "label": "添加索引器（留空=全部）",
                                        "hint": "勾选后仅添加选中的 Prowlarr 索引器，未选中的排除；留空添加全部",
                                        "chips": True,
                                        "multiple": True,
                                        "items": options,
                                    },
                                }
                            ],
                        },
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [
                                {
                                    "component": "VAlert",
                                    "props": {
                                        "type": "info",
                                        "variant": "tonal",
                                        "text": "该方式通过 Prowlarr Torznab API 扩展检索，站点由插件自动注册到站点列表，"
                                                "并随定时任务与白名单配置自动同步新增、更新与移除。"
                                                "如遇网络或 API 错误，请检查 Prowlarr 地址与 API Key 配置。",
                                    },
                                }
                            ],
                        }
                    ],
                },
            ],
        }
    ], {
        "enabled": False,
        "proxy": False,
        "host": "",
        "api_key": "",
        "cron": "0 0 * * *",
        "timeout": timeout_default,
        "indexer_sites": [],
    }


def build_page(indexers: object) -> List[dict]:
    """Build the details table from an already-loaded profile snapshot."""
    items = []
    for site in indexers if isinstance(indexers, list) else []:
        if not isinstance(site, Mapping):
            continue
        domain = str(site.get("domain") or "")
        items.append({
            "component": "tr",
            "content": [
                {"component": "td", "text": site.get("id")},
                {"component": "td", "text": f"https://{domain}/"},
                {
                    "component": "td",
                    "text": privacy_label(site.get("privacy"), site.get("public")),
                },
            ],
        })

    return [
        {
            "component": "VRow",
            "content": [
                {
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [
                        {
                            "component": "VTable",
                            "props": {"hover": True},
                            "content": [
                                {
                                    "component": "thead",
                                    "content": [
                                        {
                                            "component": "tr",
                                            "content": [
                                                {
                                                    "component": "th",
                                                    "props": {"class": "text-start ps-4"},
                                                    "text": "id",
                                                },
                                                {
                                                    "component": "th",
                                                    "props": {"class": "text-start ps-4"},
                                                    "text": "站点domain",
                                                },
                                                {
                                                    "component": "th",
                                                    "props": {"class": "text-start ps-4"},
                                                    "text": "类型",
                                                },
                                            ],
                                        }
                                    ],
                                },
                                {"component": "tbody", "content": items},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
