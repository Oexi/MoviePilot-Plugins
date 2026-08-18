# JackettExtend V3 SDK import 迁移报告

日期：2026-08-18

## 概述

将 `plugins.v3/jackettextend/__init__.py` 中的旧路径 import 全部迁移到 MoviePilot V3 稳定 SDK（`app.sdk.*`），消除兼容债务。本次为纯内部代码质量迁移：只改 import 行，不重构业务逻辑，行为零变化。

验证宿主：`/tmp/mp-v3-fix/mp-dev`（HEAD `bfb8c57`，Python 3.11.15，venv 为 `/tmp/mp-v3-test-venv`）。

## 迁移前后 import 对照表

| # | 位置 | 迁移前 | 迁移后 |
|---|------|--------|--------|
| 1 | 顶层 | `from app.core.context import TorrentInfo` | `from app.sdk.media import TorrentInfo` |
| 2 | 顶层 | `from app.log import logger` | `from app.sdk.logging import logger` |
| 3 | 顶层 | `from app.core.config import settings` | `from app.sdk.config import settings` |
| 4 | 顶层 | `from app.utils.dom import DomUtils` | `from app.sdk.utilities import DomUtils` |
| 5 | 顶层 | `from app.utils.http import RequestUtils` | `from app.sdk.network import RequestUtils` |
| 6 | 顶层 | `from app.utils.string import StringUtils` | `from app.sdk.utilities import StringUtils` |
| 7 | 函数内延迟导入（`__sync_remove_stale_sites` / `__register_site`，两处） | `from app.core.event import eventmanager, EventType` | 拆开：`from app.sdk.events import eventmanager` + `from app.schemas.types import EventType`（保留原有 `try/except ImportError` 失败保护兜底逻辑） |

## 保留的双路径兼容（未改动）

- `SitesHelper` 的 try/except 双路径（`app.helper.sites` / `app.application.site.sites`）——兼容旧镜像与 main 架构
- `SiteOper` 的 try/except 双路径（`app.db.site_oper` / `app.db.oper.site`）——同上
- `app.schemas` / `MediaType` / `MediaSource` 导入保持不变（schemas 是稳定公开入口）
- `app.plugins._PluginBase` 保持不变（稳定公开入口）

## 验证

### 1. 语法编译

```bash
$ python3 -m py_compile plugins.v3/jackettextend/__init__.py; echo EXIT=$?
EXIT=0

$ /tmp/mp-v3-test-venv/bin/python -m py_compile plugins.v3/jackettextend/__init__.py; echo VENV_EXIT=$?
VENV_EXIT=0
```

### 2. 静态确认旧路径已清零

```bash
$ grep -nE 'from app\.(core|utils|log)\.|import app\.(core|utils|log)\.|from app\.log' plugins.v3/jackettextend/__init__.py; echo OLD_PATH_EXIT=$?
OLD_PATH_EXIT=1
```

退出码 1 即无任何匹配：`app.core.*` / `app.utils.*` / `app.log` 顶层导入全部消失（注释中亦无残留，`grep -n 'app\.core\|app\.utils\|app\.log'` 同样无匹配）。

双路径兼容保持完整：

```bash
$ grep -nE 'app\.(helper\.sites|application\.site\.sites|db\.site_oper|db\.oper\.site)' plugins.v3/jackettextend/__init__.py
16:# devbox 等旧结构宿主位于 app.helper.sites,GitHub main 新架构位于 app.application.site.sites;
19:    from app.helper.sites import SitesHelper
22:        from app.application.site.sites import SitesHelper
169:                from app.db.site_oper import SiteOper
171:                from app.db.oper.site import SiteOper
291:                from app.db.site_oper import SiteOper
293:                from app.db.oper.site import SiteOper
```

### 3. 运行时验证（模拟宿主环境导入插件模块）

命令：

```bash
$ cd /tmp/mp-v3-fix/mp-dev && /tmp/mp-v3-test-venv/bin/python /tmp/verify_jacketextend_v3.py
```

模拟方式说明：

- `sys.path.insert(0, "/tmp/mp-v3-fix/mp-dev")` 指向宿主 `app` 包（等价 PYTHONPATH）
- `import app` 时 `app/__init__.py` 会自动安装旧路径兼容钩子，与宿主启动行为一致
- 第三方插件目录 `/tmp/jacketextend-sdk/plugins.v3` 挂载到 `app.plugins.__path__` 首部，以宿主 PluginLoader 的真实模块名 `app.plugins.jackettextend` 导入；输出中的文件路径证明加载的正是迁移后的文件
- 实例化需要宿主启动组合根装配的 Chain 运行上下文/数据端口；按宿主 `app/application/chain/data.py` 文档允许的“测试也可以登记隔离替身”方式装配最小上下文；`SystemConfigOper` 因 dev 检出库未执行 alembic 迁移（`no such table: systemconfig`）打桩。这些替身只涉及宿主内部组件，不触碰插件代码，不影响插件导入与元数据验证

实际输出：

```text
[1] SDK 符号全部导入成功:
    TorrentInfo   -> <class 'app.domain.context.TorrentInfo'>
    logger        -> <app.runtime.log.LoggerManager object at 0x7fb1707be290>
    settings      -> Settings
    DomUtils      -> <class 'app.foundation.dom.DomUtils'>
    StringUtils   -> <class 'app.sdk.string.StringUtils'>
    RequestUtils  -> <class 'app.adapters.network.http.RequestUtils'>
    eventmanager  -> <app.runtime.events.EventManager object at 0x7fb16c7fcc10>
    EventType     -> <enum 'EventType'> (SiteDeleted=<EventType.SiteDeleted: 'site.deleted'>)
[2] 插件模块导入成功: app.plugins.jackettextend
    文件 -> /tmp/jacketextend-sdk/plugins.v3/jackettextend/__init__.py
[3] 类元数据:
    plugin_name    = JackettExtend
    plugin_version = 3.2.1
[4] 实例化成功: JackettExtend
    instance.plugin_version = 3.2.1
ALL_CHECKS_PASSED
```

## 结论

1. 全部旧路径 import 已迁移至 `app.sdk.*` 稳定出口，`app.core.*` / `app.utils.*` / `app.log` 顶层导入清零，仅保留约定的架构兼容双路径
2. `git diff` 仅涉及 import 行（10 增 8 删），业务逻辑零变化
3. 语法编译、静态扫描、宿主环境真实导入与实例化元数据读取全部通过，`plugin_version == "3.2.1"` 断言成立

## 版本号说明

保持 `3.2.1` 不变。理由：这是纯内部 import 质量迁移，无功能新增、无行为修复，对外不可感知；若升版本会触发用户一次无实际意义的插件升级。如后续走发布流程，可随下一个功能版本一并发布；若团队坚持要有版本标记，可用 patch 号 `3.2.2` 作为内部质量迁移标记，但本次未改版本号。
