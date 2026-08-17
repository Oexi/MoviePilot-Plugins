# _*_ coding: utf-8 _*_
import ast
import copy
import re
import threading
import time
import xml.dom.minidom
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urlencode, quote_plus

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# I1: 双路径兼容导入 SitesHelper。
# devbox 等旧结构宿主位于 app.helper.sites,GitHub main 新架构位于 app.application.site.sites;
# 无 compat 层的宿主也能回退到规范路径,避免顶层 ImportError 导致插件整体无法加载。
try:
    from app.helper.sites import SitesHelper
except ImportError:
    try:
        from app.application.site.sites import SitesHelper
    except ImportError:
        SitesHelper = None

from app.core.context import TorrentInfo
from app.log import logger
from app.plugins import _PluginBase
from app.core.config import settings
from app.schemas import MediaType
try:
    from app.schemas import MediaSource
except ImportError:
    try:
        from app.schemas.types import MediaSource
    except ImportError:
        MediaSource = None
from app.utils.dom import DomUtils
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class JackettExtend(_PluginBase):
    # 插件名称
    plugin_name = "JackettExtend"
    # 插件描述
    plugin_desc = "扩展检索以支持Jackett站点资源"
    # 插件图标
    plugin_icon = "Jackett_A.png"
    # 插件版本
    plugin_version = "3.2.1"
    # 插件作者
    plugin_author = "jtcymc"
    # 作者主页
    author_url = "https://github.com/jtcymc"
    # 插件配置项ID前缀
    plugin_config_prefix = "jackett_extend_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _scheduler = None
    _cron = None
    _enabled = False
    _proxy = False
    _host = ""
    _api_key = ""
    _password = ""
    _indexer_sites = ""
    _indexers = []
    _fetch_ok = False
    sites_helper = None
    # 仅用于标识，避免重复注册
    jackett_domain = "jackett_extend.jtcymc"

    # E1: 索引器列表 TTL 缓存(内存 + 时间戳)
    _indexers_cache = None
    _indexers_cache_ts = 0.0
    _indexers_ttl = 600
    # H1: 保护 _indexers/_indexer_sites/_fetch_ok 共享状态的互斥锁
    _state_lock = threading.Lock()

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        # A1/A6: 初始化开始时复位共享状态，避免配置变更后沿用上一轮数据
        with self._state_lock:
            self._indexers = []
            self._fetch_ok = False
            self._indexers_cache = None
            self._indexers_cache_ts = 0.0

        if SitesHelper is not None:
            try:
                self.sites_helper = SitesHelper()
            except Exception as e:
                logger.warning(f"【{self.plugin_name}】SitesHelper 初始化失败：{type(e).__name__}：{str(e)}")
                self.sites_helper = None
        else:
            self.sites_helper = None

        # 读取配置
        if config:
            # A7: host 去除首尾空白，协议判断大小写不敏感，None 统一兜底为空串
            host = config.get("host")
            if host:
                host = str(host).strip()
                if not host.lower().startswith(("http://", "https://")):
                    host = "http://" + host
                host = host.rstrip("/")
            self._host = host or ""
            self._api_key = (config.get("api_key") or "").strip()
            self._password = config.get("password") or ""
            self._enabled = bool(config.get("enabled"))
            self._proxy = bool(config.get("proxy"))
            raw_sites = config.get("indexer_sites") or ""
            if isinstance(raw_sites, list):
                # UI 多选(VSelect multiple)保存为数组
                self._indexer_sites = [str(x).strip() for x in raw_sites if str(x).strip()]
            else:
                # API/旧配置为逗号分隔字符串
                self._indexer_sites = [x.strip() for x in str(raw_sites).split(",") if x.strip()]
            self._cron = str(config.get("cron") or "").strip() or "0 0 * * *"
        if not self._enabled:
            return
        # 停止现有任务
        self.stop_service()

        # 启动定时任务
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        cron_expr = self._cron or "0 0 * * *"
        logger.info(f"【{self.plugin_name}】 索引更新服务启动，周期：{cron_expr}")
        try:
            trigger = CronTrigger.from_crontab(cron_expr)
        except Exception as e:
            # A4: cron 表达式非法时回退默认值并告警，避免整个插件初始化崩溃
            logger.warning(
                f"【{self.plugin_name}】cron 表达式无效：{cron_expr!r}，已回退为默认 '0 0 * * *'：{type(e).__name__}：{str(e)}")
            trigger = CronTrigger.from_crontab("0 0 * * *")
        # H2: max_instances=1 + coalesce=True，避免定时任务与热更新实例并发操作
        self._scheduler.add_job(
            self.__sync_all,
            trigger,
            id=f"{self.plugin_config_prefix}sync",
            name=f"{self.plugin_name} indexer sync",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self._scheduler.print_jobs()
        self._scheduler.start()
        # 每次初始化都强制刷新并完整同步(拉取/注册/清理,与定时任务共用 __sync_all)
        self.__sync_all()

    def __sync_remove_stale_sites(self):
        """
        清理插件已注册但不再需要的站点记录（白名单/Jackett 变更）
        """
        try:
            # A1: 空列表保护——拉取成功但为空/失败时不执行破坏性清理
            with self._state_lock:
                indexers_snapshot = list(self._indexers) if isinstance(self._indexers, list) else []
            if not indexers_snapshot:
                return
            try:
                from app.db.site_oper import SiteOper
            except ImportError:
                from app.db.oper.site import SiteOper
            try:
                from app.core.event import eventmanager, EventType
            except ImportError:
                eventmanager = None
                EventType = None
            current_domains = {i.get("domain", "") for i in indexers_snapshot if i.get("domain")}
            # A3: 已知限制(单实例场景),清理仍严格按 jackett_extend. 前缀精准匹配
            prefix = f"{self.jackett_domain.split('.')[0]}."
            site_oper = SiteOper()
            for site in site_oper.list():
                if site.domain and site.domain.startswith(prefix) and site.domain not in current_domains:
                    site_oper.delete(site.id)
                    logger.info(f"【{self.plugin_name}】已清理过期站点记录: {site.domain}")
                    # A2: 删除后发送 SiteDeleted 事件,触发宿主清理搜索开关/缓存
                    if eventmanager is not None:
                        try:
                            eventmanager.send_event(EventType.SiteDeleted, {"site_id": site.id})
                        except Exception as e:
                            logger.warning(f"【{self.plugin_name}】发送 SiteDeleted 事件失败：{type(e).__name__}：{str(e)}")
        except Exception as e:
            logger.error(f"【{self.plugin_name}】清理过期站点失败: {str(e)}")

    def get_status(self):
        """
        检查连通性
        :return: True、False
        """
        if not self._api_key or not self._host:
            with self._state_lock:
                self._indexers = []
                self._fetch_ok = False
            return False
        try:
            indexers = self.get_indexers(force_refresh=True)
        except Exception as e:
            logger.error(f"【{self.plugin_name}】检查 Jackett 连通性失败：{type(e).__name__}：{str(e)}")
            indexers = None
        # A1: 仅当拉取成功(list)才置 _fetch_ok;失败(None)与成功但空([])严格区分
        with self._state_lock:
            self._indexers = indexers
            self._fetch_ok = indexers is not None
        return self._fetch_ok and len(indexers) > 0

    def __sync_all(self):
        """
        完整同步：拉取索引器列表 → 清理失效勾选 → 注册/注入 → 清理过期站点。
        定时任务与初始化共用，确保 Jackett 变更(新增/移除/白名单)自动同步到 MP。
        """
        self.get_status()
        with self._state_lock:
            fetch_ok = self._fetch_ok
            indexers_snapshot = list(self._indexers) if isinstance(self._indexers, list) else None
        if not fetch_ok or not isinstance(indexers_snapshot, list):
            # A1: 拉取失败直接返回且不清理，避免瞬态故障清空勾选/删除全部站点
            logger.debug(f"【{self.plugin_name}】索引器拉取失败，跳过本次同步清理")
            return
        self.__cleanup_stale_selection()
        for indexer in indexers_snapshot:
            domain = indexer.get("domain", "")
            if not domain:
                continue
            new_indexer = copy.deepcopy(indexer)
            try:
                if self.sites_helper is not None and hasattr(self.sites_helper, "add_indexer"):
                    # I1: 宿主 SitesHelper 无 del_indexer，索引器移除时依赖站点表清理与宿主重载回收注入
                    self.sites_helper.add_indexer(domain, new_indexer)
                else:
                    # I1: add_indexer 缺失降为 DEBUG，避免每次同步刷 WARNING
                    logger.debug(f"【{self.plugin_name}】宿主 SitesHelper 无 add_indexer，跳过内存注入: {domain}")
            except Exception as e:
                logger.error(f"【{self.plugin_name}】注入站点 {domain} 失败: {type(e).__name__}: {str(e)}")
            self.__register_site(indexer)
        self.__sync_remove_stale_sites()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    # H2: 等待执行中的任务完成后再退出，降低与新实例并发操作 DB 的竞态
                    self._scheduler.shutdown(wait=True)
                self._scheduler = None
        except Exception as e:
            logger.error(f"【{self.plugin_name}】停止插件错误: {str(e)}")

    def __update_config(self):
        """
        更新插件配置
        """
        # V3 适配：宿主 update_config() 为整体替换，必须写回全部配置项，
        # 否则 enabled/proxy 丢失导致插件重载后静默失效
        self.update_config({
            "cron": self._cron,
            "host": self._host,
            "api_key": self._api_key,
            "password": self._password,
            "indexer_sites": self._indexer_sites,
            "enabled": self._enabled,
            "proxy": self._proxy,
        })

    def __register_site(self, indexer: dict):
        """
        V3 适配：将 Jackett indexer 注册为站点写入 DB（site 表）。
        搜索链从 DB 读取有效站点，仅 add_indexer 注入内存时站点不可见。
        """
        domain = indexer.get("domain", "")
        if not domain:
            return
        try:
            # 双架构兼容：V2/V3 镜像旧路径由宿主 compat 层路由到规范路径
            try:
                from app.db.site_oper import SiteOper
            except ImportError:
                from app.db.oper.site import SiteOper
            try:
                from app.core.event import eventmanager, EventType
            except ImportError:
                eventmanager = None
                EventType = None
            site_oper = SiteOper()
            exists = site_oper.get_by_domain(domain)
            name = indexer.get("name", "")
            # 站点地址必须与插件"查看数据"给出的格式一致(https://jackett_extend.xxx/),
            # 官方 add_site 同样校正为 {scheme}://{netloc}/;torznab API 地址会导致校验失败
            url = f"https://{domain}/"
            public = 1 if indexer.get("public") else 0
            if exists:
                # B1: 更新分支只同步 name/url/public 来源字段,
                # 保留 is_active/pri/proxy 等用户站点设置不被 cron 覆盖
                site_oper.update(exists.id, {"name": name, "url": url, "public": public})
                logger.info(f"【{self.plugin_name}】已更新站点记录: {domain}")
            else:
                # 新增才写入默认启停/优先级/代理
                payload = {
                    "name": name,
                    "domain": domain,
                    "url": url,
                    "public": public,
                    "proxy": 1 if indexer.get("proxy") else 0,
                    "is_active": True,
                    "pri": 1,
                }
                try:
                    site_oper.add(**payload)
                    logger.info(f"【{self.plugin_name}】已注册站点到 DB: {domain}")
                except Exception as e:
                    # B2: 并发下重复插入等冲突,重新查询,已存在则转更新分支
                    existing = site_oper.get_by_domain(domain)
                    if not existing:
                        raise
                    site_oper.update(existing.id, {"name": name, "url": url, "public": public})
                    logger.debug(f"【{self.plugin_name}】站点已存在(并发注册),转为更新: {domain}, {type(e).__name__}: {str(e)}")
            # 通知宿主刷新站点缓存
            if eventmanager is not None:
                try:
                    eventmanager.send_event(EventType.SiteUpdated, {"domain": domain})
                except Exception as e:
                    logger.warning(f"【{self.plugin_name}】发送 SiteUpdated 事件失败：{type(e).__name__}：{str(e)}")
        except Exception as e:
            logger.error(f"【{self.plugin_name}】注册站点 {domain} 到 DB 失败: {str(e)}")

    def _parse_indexer_sites(self) -> list:
        """
        统一解析 indexer_sites 配置为小写 id 列表。
        兼容：list(UI 多选)、逗号分隔字符串(API/旧格式)、None/其他类型。
        """
        with self._state_lock:
            sites = self._indexer_sites
        if sites is None:
            return []

        cleaned = []
        if isinstance(sites, list):
            # 宿主可能把字符串化的 list 二次解析为带引号/括号的碎片元素，
            # 如 ["['thepiratebay'", "'therarbg']"]，逐个剥引号清洗
            for x in sites:
                x = str(x).strip()
                if not x:
                    continue
                # C1: 取全部引号 token(原实现只保留最后一个,勾选被静默截断)
                m = re.findall(r"[\'\"]([^\'\"]+)[\'\"]", x)
                if m:
                    cleaned.extend(t.strip() for t in m if t.strip())
                else:
                    # C1: 无引号元素再按逗号 split,兼容 "a, b" 作为单个 list 元素
                    cleaned.extend(t.strip() for t in x.strip("[]'\" ").split(",") if t.strip())
        elif isinstance(sites, str):
            s = sites.strip()
            # 方法1：字符串化的 list，如 "['thepiratebay', 'therarbg']"
            if s.startswith("[") and s.endswith("]"):
                try:
                    parsed = ast.literal_eval(s)
                    if isinstance(parsed, list):
                        cleaned.extend(str(x).strip() for x in parsed if str(x).strip())
                except Exception:
                    pass
            if not cleaned:
                # 方法2：正则提取引号包裹的 id（兼容任何引号类型/额外字符）
                quoted = re.findall(r"[\'\"]([^\'\"]+)[\'\"]", s)
                if quoted:
                    cleaned.extend(x.strip() for x in quoted if x.strip())
                else:
                    cleaned.extend(x.strip() for x in s.split(",") if x.strip())

        # 最后统一 strip/lower/去空去重
        result = []
        seen = set()
        for x in cleaned:
            x = str(x).strip().lower()
            if x and x not in seen:
                seen.add(x)
                result.append(x)
        return result

    def __cleanup_stale_selection(self):
        """
        移除 indexer_sites 中已被 Jackett 删除的索引器勾选，
        避免配置界面已勾选区域残留失效索引器（下拉 items 已无对应项）。
        """
        try:
            with self._state_lock:
                sites_snapshot = list(self._indexer_sites) if isinstance(self._indexer_sites, list) else []
                indexers_snapshot = list(self._indexers) if isinstance(self._indexers, list) else []
            if not sites_snapshot:
                return
            # A1: 空列表保护——拉取成功但为空/失败时不执行清理
            if not indexers_snapshot:
                return
            # C2: 以 indexer_id 为单一事实来源,合成名仅用于显示
            valid = {str(i.get("indexer_id") or "").strip().lower()
                     for i in indexers_snapshot if i.get("indexer_id")}
            stale = [x for x in sites_snapshot if str(x).strip().lower() not in valid]
            if stale:
                with self._state_lock:
                    self._indexer_sites = [
                        x for x in self._indexer_sites if str(x).strip().lower() in valid
                    ]
                self.__update_config()
                logger.info(f"【{self.plugin_name}】已清理失效勾选: {stale}")
        except Exception as e:
            logger.error(f"【{self.plugin_name}】清理失效勾选失败: {str(e)}")

    def search_torrents(self, site: dict, keyword: str = None, mtype: Optional[MediaType] = None,
                        cat: Optional[str] = None, page: Optional[int] = 0, **kwargs) -> \
            List[
                TorrentInfo]:
        """
        使用 Jackett Torznab API 根据关键字检索种子
        :param site:  站点
        :param keyword:  搜索关键词（为空时获取最新资源，refresh_torrents 语义）
        :param mtype:  媒体类型
        :param page:  页码（插件不支持翻页，page>0 直接返回空避免重复结果）
        :return: 资源列表
        """
        results = []
        if not site:
            return results

        # D1: 以 domain 前缀识别本插件站点，不依赖 name 前缀
        # （新架构宿主会把 DB 站点行 name 与注入 profile 合并，name 取自 DB 时会静默误判）
        domain = str(site.get("domain") or "").strip()
        if domain.lower().startswith(("http://", "https://")):
            try:
                domain = StringUtils.get_url_domain(domain) or domain
            except Exception:
                pass
        if not domain.startswith("jackett_extend."):
            # 非本插件站点属正常分发，DEBUG 即可，避免百站搜索刷屏
            logger.debug(f"【{self.plugin_name}】站点非本插件注册，交由其他模块处理：name={site.get('name')}, domain={domain!r}")
            return results
        # D1: indexer id 取 domain 第一个点之后的全部内容，兼容带点的 id
        indexer_id = domain.split(".", 1)[1] if "." in domain else ""
        if not indexer_id:
            # D1: 前缀命中但无法解析 id 才是真正的识别失败，用 WARNING 便于诊断
            logger.warning(f"【{self.plugin_name}】站点识别失败，无法从 domain 解析 indexer id：name={site.get('name')}, domain={domain!r}")
            return results

        # D5: 不支持翻页；page>0 直接返回空，避免宿主重复请求同页造成结果重复合并
        try:
            page_num = int(page or 0)
        except (TypeError, ValueError):
            page_num = 0
        if page_num > 0:
            logger.debug(f"【{self.plugin_name}】不支持翻页，跳过 page={page_num} 请求以避免重复结果：domain={domain}")
            return results

        # D4: keyword 为空时使用 Jackett 空查询获取最新资源(refresh_torrents/RSS 刷新)
        keyword = keyword or ""
        masked_keyword = self.__mask_keyword(keyword)
        api_url = ""
        try:
            # D6: 搜索热路径日志降为 DEBUG,关键词仅 DEBUG 输出且脱敏
            logger.debug(f"【{self.plugin_name}】开始检索 Indexer：\"{site.get('name')}\"，关键词：\"{masked_keyword}\"")

            params = {
                "apikey": self._api_key,
                "t": "search",
                "q": keyword,
            }
            # BUGFIX: 透传用户在站点浏览弹窗中显式选择的分类 ID。
            # 宿主 /site/{id}/category 返回的条目 id 会以逗号分隔字符串回传
            # (如 "2000,3000")，Jackett torznab cat 参数同样接受该格式，
            # 让 UI 分类筛选真实生效；未显式选择时不传 cat，
            # 保持按标题/媒体类型兜底过滤的既有语义。
            cat_value = str(cat or "").strip()
            if cat_value:
                cat_value = re.sub(r"\s+", "", cat_value)
                if re.fullmatch(r"\d+(,\d+)*", cat_value):
                    params["cat"] = cat_value
                else:
                    logger.debug(
                        f"【{self.plugin_name}】忽略非分类 ID 格式的 cat 参数：{cat_value!r}")
            query_string = urlencode(params, quote_via=quote_plus)
            api_url = f"{self._host.rstrip('/')}/api/v2.0/indexers/{indexer_id}/results/torznab/?{query_string}"

            result_array = self.__parse_torznab_xml(api_url, site=site, mtype=mtype, keyword=keyword)

            if not result_array:
                # D6: 无结果是常态,降为 DEBUG
                logger.debug(f"【{self.plugin_name}】Indexer：\"{site.get('name')}\" 未检索到数据，关键词：\"{masked_keyword}\"")
                return results

            logger.debug(f"【{self.plugin_name}】Indexer：\"{site.get('name')}\" 返回数据：{len(result_array)} 条")
            results.extend(result_array)

        except Exception as e:
            # D8: 异常日志附带 URL/站点/关键词(脱敏)/异常类型
            logger.error(
                f"【{self.plugin_name}】检索出错：site={site.get('name')}, indexer={indexer_id}, "
                f"url={api_url or '-'}, 关键词={masked_keyword or '-'}, "
                f"类型={type(e).__name__}：{str(e)}")

        return results

    def get_indexers(self, filter_selected: bool = True, force_refresh: bool = False):
        """
        获取配置的 Jackett Indexer 信息
        :param filter_selected: True 按白名单过滤(留空=全部),False 返回完整列表
        :param force_refresh: True 强制实时拉取(初始化/cron),False 优先使用 TTL 缓存
        :return: Indexer 列表；拉取成功但为空返回 []，拉取失败返回 None
        """
        now = time.time()
        with self._state_lock:
            cached = self._indexers_cache
            cached_ts = self._indexers_cache_ts

        if force_refresh:
            # 初始化/cron 同步强制刷新;失败返回 None,不复用旧缓存,
            # 避免把旧数据误判为"本次拉取成功"而触发破坏性清理(A1)
            raw = self.__fetch_indexers()
            if raw is None:
                return None
            with self._state_lock:
                self._indexers_cache = raw
                self._indexers_cache_ts = time.time()
        elif isinstance(cached, list) and cached_ts and (now - cached_ts) < self._indexers_ttl:
            # E1: TTL 内直接使用缓存
            raw = cached
        else:
            # E1/G4: 表单/详情页缓存过期或缺失时尝试刷新;失败用旧缓存兜底,
            # 并顺延时间戳,避免 Jackett 故障时每次打开页面都阻塞在超时请求上
            raw = self.__fetch_indexers()
            if raw is not None:
                with self._state_lock:
                    self._indexers_cache = raw
                    self._indexers_cache_ts = time.time()
            elif isinstance(cached, list):
                raw = cached
                with self._state_lock:
                    self._indexers_cache_ts = time.time()
            else:
                return None

        if not filter_selected:
            return list(raw)
        selected = self._parse_indexer_sites()
        if not selected:
            return list(raw)
        # C2: 白名单过滤统一以 indexer_id 为单一事实来源,合成名仅用于显示
        return [i for i in raw if str(i.get("indexer_id") or "").strip().lower() in selected]

    def __fetch_indexers(self):
        """
        实时从 Jackett 拉取并构造 indexer 列表（完整列表,不过滤白名单）。
        :return: 成功返回 list(可能为空);失败返回 None
        """
        if not self._host or not self._api_key:
            return None
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": settings.USER_AGENT,
            "X-Api-Key": self._api_key,
            "Accept": "application/json, text/javascript, */*; q=0.01"
        }

        cookie = None
        session = requests.session()
        try:
            # E2: 密码为空时跳过登录;仅通过 data 提交密码,不放入 URL query string
            if self._password:
                login_url = f"{self._host.rstrip('/')}/UI/Dashboard"
                try:
                    login_res = RequestUtils(headers=headers, session=session).post_res(
                        url=login_url,
                        data={"password": self._password},
                        proxies=settings.PROXY if self._proxy else None
                    )
                except Exception as e:
                    logger.warning(f"【{self.plugin_name}】Jackett 登录请求异常：{type(e).__name__}：{str(e)}")
                    login_res = None
                if login_res is not None and session.cookies:
                    cookie = session.cookies.get_dict()
                elif self._password:
                    logger.warning(f"【{self.plugin_name}】Jackett 登录失败，无法获取 cookie")

            indexer_query_url = f"{self._host.rstrip('/')}/api/v2.0/indexers?configured=true"
            ret = RequestUtils(headers=headers, cookies=cookie).get_res(
                indexer_query_url,
                proxies=settings.PROXY if self._proxy else None
            )

            # E3: 校验状态码/Content-Type/数据类型,json 只解析一次
            if ret is None:
                logger.warning(f"【{self.plugin_name}】拉取 indexers 请求失败：{indexer_query_url}")
                return None
            if ret.status_code != 200:
                logger.warning(f"【{self.plugin_name}】拉取 indexers 失败,HTTP {ret.status_code}：{indexer_query_url}")
                return None
            content_type = (ret.headers.get("Content-Type") or "").lower()
            if "json" not in content_type:
                logger.warning(f"【{self.plugin_name}】拉取 indexers 响应非 JSON(Content-Type={content_type!r})")
                return None
            try:
                raw_indexers = ret.json()
            except ValueError as e:
                logger.warning(f"【{self.plugin_name}】拉取 indexers JSON 解析失败：{type(e).__name__}：{str(e)}")
                return None
            if not isinstance(raw_indexers, list):
                logger.warning(
                    f"【{self.plugin_name}】拉取 indexers 响应类型异常"
                    f"(期望 list,实际 {type(raw_indexers).__name__})")
                return None
        except Exception as e:
            logger.error(f"【{self.plugin_name}】获取 Jackett indexers 失败：{type(e).__name__}：{str(e)}")
            return None
        finally:
            # E4: 明确关闭 session,避免依赖 GC 回收连接池
            try:
                session.close()
            except Exception:
                pass

        logger.debug(f"【{self.plugin_name}】Jackett indexers: {[v.get('id') for v in raw_indexers]}")
        indexers = []
        for v in raw_indexers:
            if not isinstance(v, dict):
                continue
            indexer_id = v.get("id")
            indexer_name = v.get("name")
            if not indexer_id or not indexer_name:
                continue

            # V3 适配：解析 Jackett caps 生成媒体类型分类。
            # 宿主索引器契约要求 category 为 {media_type: [分类条目 dict]}，
            # 条目含 id(选择后回传的分类 ID)与 cat/desc(展示名)。
            # 之前用布尔 True 占位虽能通过媒体类型列表的 truthy 判断，
            # 但点击站点打开浏览弹窗时，宿主 GET /site/{id}/category 会迭代
            # category.values() 并把每个值当作条目列表，布尔值触发
            # 'bool' object is not iterable，页面弹出"未知错误"。
            # V3 音乐搜索的站点列表依赖 indexer.category.music 字段，
            # 无 category 的索引器在音乐搜索中被过滤（电影/电视默认放行）。
            category = {}
            for cap in (v.get("caps") or []):
                if not isinstance(cap, dict):
                    continue
                cap_id = str(cap.get("ID", "")).strip()
                if not cap_id:
                    continue
                cap_name = str(cap.get("Name") or "").strip() or cap_id
                entry = {"id": cap_id, "cat": cap_name, "desc": cap_name}
                if cap_id.startswith("2000"):
                    category.setdefault("movie", []).append(entry)
                elif cap_id.startswith("5000"):
                    category.setdefault("tv", []).append(entry)
                elif cap_id.startswith("3000"):
                    category.setdefault("music", []).append(entry)

            # E5: public 由 Jackett privacy 字段判断;proxy 与插件配置联动
            privacy = str(v.get("privacy") or "").strip().lower()
            indexers.append({
                "id": f'{self.plugin_name}-{indexer_name}',
                "indexer_id": indexer_id,
                "name": f'{self.plugin_name}-{indexer_name}',
                "url": f'{self._host.rstrip("/")}/api/v2.0/indexers/{indexer_id}/results/torznab/',
                "domain": self.jackett_domain.replace(self.plugin_author, str(indexer_id)),
                "public": privacy == "public",
                "proxy": bool(self._proxy),
                "category": category,
            })

        logger.info(f"【{self.plugin_name}】获取到 {len(indexers)} 个 Jackett indexers")
        return indexers

    def get_module(self) -> Dict[str, Any]:
        """
        获取插件模块声明，用于胁持系统模块实现（方法名：方法实现）
        {
            "id1": self.xxx1,
            "id2": self.xxx2,
        }
        """
        # V3 适配：V3 搜索链走异步模块(async_search_torrents)，必须一并注册
        def _wrapped_search(*args, **kwargs):
            return self.search_torrents(*args, **kwargs)

        def _wrapped_page_size(*args, **kwargs):
            # D5: 明确告知宿主本插件站点不支持翻页(返回 None)
            return None

        return {
            "search_torrents": _wrapped_search,
            "async_search_torrents": _wrapped_search,
            "refresh_torrents": _wrapped_search,
            "async_refresh_torrents": _wrapped_search,
            "get_search_page_size": _wrapped_page_size,
        }

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API说明"
        }]
        """

        pass

    @staticmethod
    def __safe_int(value):
        """D2: 转为 int,解析失败回退 0"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def __safe_float(value):
        """D2: 转为 float,解析失败回退 0"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def __safe_float_none(value):
        """促销因子解析失败回退 None(避免把非法值误判为 0/free)"""
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def __mask_keyword(keyword):
        """D6/D8: 日志中的关键词脱敏,仅在 DEBUG/异常上下文使用"""
        if not keyword:
            return ""
        if len(keyword) <= 4:
            return "*" * len(keyword)
        return f"{keyword[:2]}{'*' * min(len(keyword) - 2, 6)}({len(keyword)})"

    def __parse_torznab_xml(self, url, site: dict = None, mtype: Optional[MediaType] = None,
                            keyword: str = None) -> List[TorrentInfo]:
        """
        从 torznab XML 中解析种子信息
        :param url: XML 数据的 URL
        :return: TorrentInfo 列表
        """
        if not url:
            return []
        try:
            # F2: 超时由 60s 降至 30s;不加重试,避免放大重复请求
            ret = RequestUtils(timeout=30).get_res(url,
                                                   proxies=settings.PROXY if self._proxy else None)
        except Exception as e:
            logger.error(f"【{self.plugin_name}】torznab 请求异常：url={url}, 类型={type(e).__name__}：{str(e)}")
            return []
        if ret is None or not ret.text:
            logger.debug(f"【{self.plugin_name}】torznab 空响应：url={url}")
            return []

        # F1: 校验状态码与 Content-Type;JSON 错误体不进 XML 解析
        content_type = (ret.headers.get("Content-Type") or "").lower()
        if ret.status_code != 200:
            detail = f"HTTP {ret.status_code}"
            if "json" in content_type:
                try:
                    err_body = ret.json()
                    if isinstance(err_body, dict) and err_body.get("error"):
                        detail = f"{detail}, error={err_body.get('error')}"
                except Exception:
                    pass
            logger.warning(f"【{self.plugin_name}】Jackett torznab API 错误：url={url}, {detail}")
            return []
        if "json" in content_type:
            try:
                err_body = ret.json()
            except Exception:
                err_body = None
            error = err_body.get("error") if isinstance(err_body, dict) else None
            logger.warning(f"【{self.plugin_name}】Jackett 返回 JSON 错误体而非 torznab XML：url={url}, error={error!r}")
            return []

        torrents = []
        try:
            # F3: 保持 stdlib minidom,不加新依赖。torznab:attr 命名空间取值依赖
            # getAttribute,DOM 树整体解析实现简单稳定;ElementTree.iterparse 流式解析
            # 需自行处理命名空间且收益有限,故保留 minidom 并在此标注。
            dom_tree = xml.dom.minidom.parseString(ret.text)
            root_node = dom_tree.documentElement
            items = root_node.getElementsByTagName("item")
        except Exception as e:
            # F1: XML 解析失败降为 WARNING,不输出完整 traceback 刷屏
            logger.warning(f"【{self.plugin_name}】torznab XML 解析失败：url={url}, 类型={type(e).__name__}：{str(e)}")
            return []

        for item in items:
            try:
                # 标题
                title = DomUtils.tag_value(item, "title", default="")
                if not title:
                    continue
                # 种子链接
                enclosure = DomUtils.tag_value(item, "enclosure", "url", default="")
                if not enclosure:
                    continue
                # 描述
                description = DomUtils.tag_value(item, "description", default="")
                # 种子大小
                size = DomUtils.tag_value(item, "size", default=0)
                # 种子页面
                page_url = DomUtils.tag_value(item, "comments", default="")
                # 发布时间
                pubdate = DomUtils.tag_value(item, "pubDate", default="")
                if pubdate:
                    pubdate = StringUtils.unify_datetime_str(pubdate)
                # 做种数
                seeders = 0
                # 下载数
                peers = 0
                # imdbid
                imdbid = ""
                # 促销因子/HR
                uploadvolumefactor = None
                downloadvolumefactor = None
                hit_and_run = False

                torznab_attrs = item.getElementsByTagName("torznab:attr")
                for torznab_attr in torznab_attrs:
                    name = torznab_attr.getAttribute('name')
                    value = torznab_attr.getAttribute('value')
                    if name == "seeders":
                        seeders = value
                    elif name == "peers":
                        peers = value
                    elif name == "downloadvolumefactor":
                        downloadvolumefactor = value
                    elif name == "uploadvolumefactor":
                        uploadvolumefactor = value
                    elif name == "hit_and_run":
                        hit_and_run = str(value).strip().lower() in ("1", "true", "yes")
                    elif name == "imdbid":
                        imdbid = str(value).strip()

                # D3: imdbid 映射为 media_source/media_id 媒体身份
                media_source = None
                media_id = None
                if imdbid:
                    media_id = imdbid
                    if MediaSource is not None:
                        try:
                            media_source = MediaSource.IMDb
                        except AttributeError:
                            media_source = None

                tmp_dict = TorrentInfo(
                    title=title,
                    enclosure=enclosure,
                    description=description,
                    # D2: seeders/peers 转 int,size 转 float,解析失败回退 0
                    size=self.__safe_float(size),
                    seeders=self.__safe_int(seeders),
                    peers=self.__safe_int(peers),
                    # V3 适配：显示真实站点名（原版硬编码 jackett_domain 导致结果来源显示无意义域名）
                    site_name=site.get("name", self.plugin_name) if site else self.plugin_name,
                    page_url=page_url,
                    # D3: pubdate/促销因子/HR 传入 TorrentInfo,支持发布时长与促销过滤
                    pubdate=pubdate or None,
                    uploadvolumefactor=self.__safe_float_none(uploadvolumefactor),
                    downloadvolumefactor=self.__safe_float_none(downloadvolumefactor),
                    hit_and_run=bool(hit_and_run),
                    media_source=media_source,
                    media_id=media_id,
                    # V3 适配：填种子分类。MP 音乐匹配（_matching_music_torrents）要求
                    # torrent.category == MUSIC，原版不填导致音乐搜索全部被过滤。
                    # Jackett 的 torznab category 值（nyaa 150332/118685 等源 ID）与标准
                    # 分类不一致，无法可靠映射，直接用宿主搜索 mtype 兜底（音乐搜索时
                    # mtype 必为 MUSIC，再由上层标题+艺术家匹配筛除无关资源）。
                    category=mtype.value if mtype else None,
                )
                torrents.append(tmp_dict)
            except Exception as e:
                # D8: item 级解析异常附带 URL 与异常类型,降为 DEBUG 避免刷屏
                logger.debug(
                    f"【{self.plugin_name}】torznab item 解析失败,已跳过：url={url}, "
                    f"类型={type(e).__name__}：{str(e)}")
                continue

        return torrents

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 动态生成索引器多选选项(完整列表,不受白名单过滤影响,否则无法取消勾选)
        # E1/G4: 优先使用 TTL 缓存,避免每次打开表单都请求 Jackett
        site_options = []
        try:
            for idx in self.get_indexers(filter_selected=False) or []:
                site_options.append({"title": f"{idx.get('name', '')} ({idx.get('indexer_id', '')})",
                                     # C2: 以 indexer_id 为单一事实来源,合成名仅用于显示
                                     "value": idx.get('indexer_id')})
        except Exception as e:
            logger.warning(f"【{self.plugin_name}】获取索引器选项失败: {str(e)}")
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
                                            'items': site_options
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
            "enabled": False,
            "proxy": False,
            "host": "",
            "api_key": "",
            "password": "",
            "cron": "0 0 * * *",
            # G1: 补齐 indexer_sites 默认键,与保存配置结构一致
            "indexer_sites": []
        }

    def _ensure_sites_loaded(self) -> bool:
        """
        确保 self._indexers 已加载数据，若为空则尝试重新加载。
        :return: 成功加载返回 True，否则 False
        """
        with self._state_lock:
            current = self._indexers
        if isinstance(current, list) and len(current) > 0:
            return True

        # E1/G4: 详情页优先使用 TTL 缓存,不强制实时请求
        indexers = self.get_indexers(filter_selected=True)
        if indexers is None:
            return False
        with self._state_lock:
            self._indexers = indexers
            self._fetch_ok = True
        return len(indexers) > 0

    def get_page(self) -> List[dict]:
        """
            拼装插件详情页面，需要返回页面配置，同时附带数据
        """
        if not self._ensure_sites_loaded():
            return []

        with self._state_lock:
            indexers = list(self._indexers) if isinstance(self._indexers, list) else []
        items = []
        for site in indexers:
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
                        'text': site.get("public")
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
                                                        'text': '是否公开'
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
