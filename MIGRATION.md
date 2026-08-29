# JackettExtend V3-only 迁移说明

JackettExtend 现仅支持当前 MoviePilot V3，插件版本为 `3.2.16`，由 Oexi 独立维护。原始项目作者为 jtcymc，原始项目地址为 <https://github.com/jtcymc/MoviePilot-PluginsV2>。

V2 版 `ProwlarrExtend` 已删除，仓库不再提供 MoviePilot V2 插件；`package.json` 和 `package.v2.json` 也已移除。

V3 实现只使用当前公开入口：网络适配器来自 `app.sdk.network`，媒体模型来自 `app.sdk.media`，日志、配置、工具和事件来自 `app.sdk.*`，插件基类来自 `app.plugins`，站点数据库操作来自 `app.db.oper.site`，枚举来自当前 `app.schemas`/`app.schemas.types` 合同。

当前 V3 的按站点同步/异步搜索及刷新入口仍将系统模块作为调用边界，因此插件保留最小的生命周期管理桥接，覆盖 `ChainBase.search_site_torrents`、`async_search_site_torrents`、`refresh_torrents` 和 `async_refresh_torrents`（宿主未提供的刷新入口会跳过），且只接管非空 Mapping 中命中对应虚拟站点 predicate 的站点。普通站点、`site={}` 的全局插件调用以及 `get_search_page_size` 继续由宿主/当前 `run_module(..., public_to_plugins=True)` 分发处理；page-size provider 由 `JackettExtend.get_module()` 注册。启停、重载和多实例切换会恢复或更新唯一桥接所有者。

已注册站点使用既有 `jackett_extend.<indexer>` 持久化域名前缀，以便现有 V3 数据在升级后继续识别；这不是对 V2 实现的支持。

插件禁用或停止时会按各自保留的虚拟域名前缀清理已注册站点，并发送 `SiteDeleted` 事件；JackettExtend 与 ProwlarrExtend 的站点命名空间彼此隔离。
