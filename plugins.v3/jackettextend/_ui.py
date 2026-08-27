# _*_ coding: utf-8 _*_
"""Deterministic UI dictionaries for the JackettExtend plugin.

This module intentionally has no MoviePilot, network, logging, or plugin
state dependencies.  The plugin entry point keeps loading, state snapshots,
and error handling around these pure builders.
"""

import copy
from typing import Any, Dict, List, Tuple

from ._indexers import privacy_label


def build_form(
        site_options: object,
        timeout_default: int = 30,
        timeout_min: int = 5,
        timeout_max: int = 120,
) -> Tuple[List[dict], Dict[str, Any]]:
    """Build the plugin configuration form and its default values.

    ``site_options`` is copied before it is embedded in the result so callers
    can safely reuse their list after rendering the form.
    """
    options = copy.deepcopy(site_options) if isinstance(site_options, list) else []
    timeout_default_text = str(timeout_default)
    return [
        {
            'component': 'VForm',
            'content': [
                # G2: 修复 VRow 嵌套,各行平级
                {
                    'component': 'VRow',
                    'content': [
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                                'md': 6
                            },
                            'content': [
                                {
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'enabled',
                                        'label': '启用插件',
                                    }
                                }
                            ]
                        },
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                                'md': 6
                            },
                            'content': [
                                {
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'timeout',
                                        'label': '搜索超时（秒）',
                                        'placeholder': timeout_default_text,
                                        'hint': f'仅用于 Torznab 搜索，范围 {timeout_min}-{timeout_max} 秒，超出范围会自动限制'
                                    }
                                }
                            ]
                        },
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                                'md': 6
                            },
                            'content': [
                                {
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'proxy',
                                        'label': '使用代理服务器',
                                    }
                                }
                            ]
                        },
                    ]
                },
                {
                    'component': 'VRow',
                    'content': [
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                                'md': 6
                            },
                            'content': [
                                {
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'host',
                                        'label': 'Jackett地址',
                                        'placeholder': 'http://127.0.0.1:9117',
                                        'hint': 'Jackett访问地址和端口，如为https需加https://前缀。注意需要先在Jackett中添加indexer，才能正常测试通过和使用'
                                    }
                                }
                            ]
                        },
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                                'md': 6
                            },
                            'content': [
                                {
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'api_key',
                                        'label': 'Api Key',
                                        'placeholder': '',
                                        'hint': 'Jackett管理界面右上角复制API Key'
                                    }
                                }
                            ]
                        }
                    ]
                },
                {
                    'component': 'VRow',
                    'content': [
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                                'md': 6
                            },
                            'content': [
                                {
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'password',
                                        'label': '密码',
                                        'placeholder': '',
                                        'hint': 'Jackett管理界面中配置的Admin password，如未配置可为空',
                                        'type': 'password'
                                    }
                                }
                            ]
                        },
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                                'md': 6
                            },
                            'content': [
                                {
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'cron',
                                        'label': '更新周期',
                                        'placeholder': '0 0 * * *',
                                        'hint': '索引列表更新周期，支持5位cron表达式，默认每24小时运行一次'
                                    }
                                }
                            ]
                        },
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                                'md': 6
                            },
                            'content': [
                                {
                                    'component': 'VSelect',
                                    'props': {
                                        'model': 'indexer_sites',
                                        'label': '添加索引器(留空=全部)',
                                        'hint': '勾选后仅添加选中的Jackett索引器，未选中的排除；留空添加全部',
                                        'chips': True,
                                        'multiple': True,
                                        'items': options
                                    }
                                }
                            ]
                        }
                    ]
                },
                {
                    'component': 'VRow',
                    'content': [
                        {
                            'component': 'VCol',
                            'props': {
                                'cols': 12,
                            },
                            'content': [
                                {
                                    'component': 'VAlert',
                                    'props': {
                                        'type': 'info',
                                        'variant': 'tonal',
                                        # G3: 删除误导用户忽略 NoneType 错误的文案,
                                        # 改为自动注册与排障说明
                                        'text': '该方式通过 Jackett Torznab API 扩展检索，站点由插件自动注册到站点列表，'
                                                '并随定时任务与白名单配置自动同步新增、更新与移除。'
                                                '如遇网络或 API 错误，请查看日志确认 Jackett 地址、Api Key 与密码配置正确。'
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    ], {
        'enabled': False,
        'proxy': False,
        'host': '',
        'api_key': '',
        'password': '',
        'cron': '0 0 * * *',
        'timeout': timeout_default,
        # G1: 补齐 indexer_sites 默认键,与保存配置结构一致
        'indexer_sites': []
    }


def build_page(indexers: object) -> List[dict]:
    """Build the plugin details table from an already-loaded snapshot."""
    items = []
    for site in indexers if isinstance(indexers, list) else []:
        items.append({
            'component': 'tr',
            'content': [
                {
                    'component': 'td',
                    'text': site.get("id")
                },
                {
                    'component': 'td',
                    # G3: 与 DB 中站点 url 格式一致(带尾斜杠)
                    'text': f"https://{site.get('domain')}/"
                },
                {
                    'component': 'td',
                    'text': privacy_label(site.get("privacy"), site.get("public"))
                }
            ]
        })

    return [
        {
            'component': 'VRow',
            'content': [
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 12
                    },
                    'content': [
                        {
                            'component': 'VTable',
                            'props': {
                                'hover': True
                            },
                            'content': [
                                {
                                    'component': 'thead',
                                    'content': [
                                        {
                                            'component': 'tr',
                                            'content': [
                                                {
                                                    'component': 'th',
                                                    'props': {
                                                        'class': 'text-start ps-4'
                                                    },
                                                    'text': 'id'
                                                },
                                                {
                                                    'component': 'th',
                                                    'props': {
                                                        'class': 'text-start ps-4'
                                                    },
                                                    'text': '站点domain'
                                                },
                                                {
                                                    'component': 'th',
                                                    'props': {
                                                        'class': 'text-start ps-4'
                                                    },
                                                    'text': '类型'
                                                }
                                            ]
                                        }
                                    ]
                                },
                                {
                                    'component': 'tbody',
                                    'content': items
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    ]
