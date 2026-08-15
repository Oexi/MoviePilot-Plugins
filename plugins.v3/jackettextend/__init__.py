# _*_ coding: utf-8 _*_
import copy
import traceback
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timedelta
import xml.dom.minidom
from urllib.parse import urlencode, quote_plus

import pytz
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.helper.sites import SitesHelper

from app.core.context import TorrentInfo
from app.log import logger
from app.plugins import _PluginBase
from app.core.config import settings
from app.schemas import MediaType
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
    plugin_version = "3.1.9"
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

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        self.sites_helper = SitesHelper()
        # 读取配置
        if config:
            self._host = config.get("host")
            if self._host:
                if not self._host.startswith('http'):
                    self._host = "http://" + self._host
                if self._host.endswith('/'):
                    self._host = self._host.rstrip('/')
            self._api_key = config.get("api_key")
            self._password = config.get("password")
            self._enabled = config.get("enabled")
            self._proxy = config.get("proxy")
            raw_sites = config.get("indexer_sites") or ""
            if isinstance(raw_sites, list):
                # UI 多选(VSelect multiple)保存为数组
                self._indexer_sites = [str(x).strip() for x in raw_sites if str(x).strip()]
            else:
                # API/旧配置为逗号分隔字符串
                self._indexer_sites = [x.strip() for x in str(raw_sites).split(",") if x.strip()]
            self._cron = config.get("cron") or "0 0 * * *"
        if not self._enabled:
            return
        # 停止现有任务
        self.stop_service()

        # 启动定时任务
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        if self._cron:
            logger.info(f"【{self.plugin_name}】 索引更新服务启动，周期：{self._cron}")
            self._scheduler.add_job(self.__sync_all, CronTrigger.from_crontab(self._cron))

        if self._cron:
            # 启动服务
            self._scheduler.print_jobs()
            self._scheduler.start()
        # 每次初始化都重新拉取并完整同步(拉取/注册/清理,与定时任务共用 __sync_all)
        self.__sync_all()

    def __sync_remove_stale_sites(self):
        """
        清理插件已注册但不再需要的站点记录（白名单/Jackett 变更）
        """
        try:
            try:
                from app.db.site_oper import SiteOper
            except ImportError:
                from app.db.oper.site import SiteOper
            current_domains = {i.get("domain", "") for i in self._indexers if i.get("domain")}
            prefix = f"{self.jackett_domain.split('.')[0]}."
            site_oper = SiteOper()
            for site in site_oper.list():
                if site.domain and site.domain.startswith(prefix) and site.domain not in current_domains:
                    site_oper.delete(site.id)
                    logger.info(f"【{self.plugin_name}】已清理过期站点记录: {site.domain}")
        except Exception as e:
            logger.error(f"【{self.plugin_name}】清理过期站点失败: {str(e)}")

    def get_status(self):
        """
        检查连通性
        :return: True、False
        """
        if not self._api_key or not self._host:
            return False
        self._indexers = self.get_indexers()
        # 拉取成功标志：请求链路正常即为成功（过滤后为空也视为成功，可安全清理）
        self._fetch_ok = isinstance(self._indexers, list)
        return True if self._fetch_ok and len(self._indexers) > 0 else False

    def __sync_all(self):
        """
        完整同步：拉取索引器列表 → 清理失效勾选 → 注册/注入 → 清理过期站点。
        定时任务与初始化共用，确保 Jackett 变更(新增/移除/白名单)自动同步到 MP。
        """
        self.get_status()
        if not self._fetch_ok:
            return
        self.__cleanup_stale_selection()
        for indexer in self._indexers:
            domain = indexer.get("domain", "")
            if not domain:
                continue
            new_indexer = copy.deepcopy(indexer)
            try:
                if hasattr(self.sites_helper, 'add_indexer'):
                    self.sites_helper.add_indexer(domain, new_indexer)
                else:
                    logger.warning(f"【{self.plugin_name}】宿主 SitesHelper 无 add_indexer，跳过注入: {domain}")
            except Exception as e:
                logger.error(f"【{self.plugin_name}】注入站点 {domain} 失败: {str(e)}")
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
                    self._scheduler.shutdown()
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
            from app.db.site_oper import SiteOper
            from app.core.event import eventmanager, EventType
            site_oper = SiteOper()
            exists = site_oper.get_by_domain(domain)
            payload = {
                "name": indexer.get("name", ""),
                "domain": domain,
                # 站点地址必须与插件"查看数据"给出的格式一致(https://jackett_extend.xxx/),
                # 官方 add_site 同样校正为 {scheme}://{netloc}/;torznab API 地址会导致校验失败
                "url": f"https://{domain}/",
                "public": 1 if indexer.get("public") else 0,
                "proxy": 1 if indexer.get("proxy") else 0,
                "is_active": True,
                "pri": 1,
            }
            if exists:
                site_oper.update(exists.id, payload)
                logger.info(f"【{self.plugin_name}】已更新站点记录: {domain}")
            else:
                site_oper.add(**payload)
                logger.info(f"【{self.plugin_name}】已注册站点到 DB: {domain}")
            # 通知宿主刷新站点缓存
            eventmanager.send_event(EventType.SiteUpdated, {"domain": domain})
        except Exception as e:
            logger.error(f"【{self.plugin_name}】注册站点 {domain} 到 DB 失败: {str(e)}")

    def _parse_indexer_sites(self) -> list:
        """
        统一解析 indexer_sites 配置为小写 id 列表。
        兼容：list(UI 多选)、逗号分隔字符串(API/旧格式)、None/其他类型。
        """
        sites = self._indexer_sites
        if isinstance(sites, list):
            # 宿主可能把字符串化的 list 二次解析为带引号/括号的碎片元素，
            # 如 ["['thepiratebay'", "'therarbg']"]，逐个剥引号清洗
            import re
            cleaned = []
            for x in sites:
                x = str(x).strip()
                m = re.findall(r"[\'\"]([^\'\"]+)[\'\"]", x)
                if m:
                    x = m[-1]
                else:
                    x = x.strip("[]'\" ")
                x = x.strip().lower()
                if x:
                    cleaned.append(x)
            return cleaned
        if isinstance(sites, str):
            s = sites.strip()
            # 方法1：字符串化的 list，如 "['thepiratebay', 'therarbg']"
            if s.startswith("[") and s.endswith("]"):
                try:
                    import ast
                    parsed = ast.literal_eval(s)
                    if isinstance(parsed, list):
                        return [str(x).strip().lower() for x in parsed if str(x).strip()]
                except Exception:
                    pass
            # 方法2：正则提取引号包裹的 id（兼容任何引号类型/额外字符）
            import re
            quoted = re.findall(r"[\'\"]([^\'\"]+)[\'\"]", s)
            if quoted:
                return [x.strip().lower() for x in quoted if x.strip()]
            return [x.strip().lower() for x in s.split(",") if x.strip()]
        return []

    def __cleanup_stale_selection(self):
        """
        移除 indexer_sites 中已被 Jackett 删除的索引器勾选，
        避免配置界面已勾选区域残留失效索引器（下拉 items 已无对应项）。
        """
        try:
            if not self._indexer_sites:
                return
            valid = set()
            for i in self._indexers:
                iid = str(i.get("indexer_id") or "").strip().lower()
                sid = str(i.get("id") or "").strip().lower()
                if iid:
                    valid.add(iid)
                if sid:
                    valid.add(sid)
            stale = [x for x in self._indexer_sites if str(x).strip().lower() not in valid]
            if stale:
                self._indexer_sites = [x for x in self._indexer_sites if str(x).strip().lower() in valid]
                self.__update_config()
                logger.info(f"【{self.plugin_name}】已清理失效勾选: {stale}")
        except Exception as e:
            logger.error(f"【{self.plugin_name}】清理失效勾选失败: {str(e)}")

    def search_torrents(self, site: dict, keyword: str, mtype: Optional[MediaType] = None,
                        cat: Optional[str] = None, page: Optional[int] = 0, **kwargs) -> \
            List[
                TorrentInfo]:
        """
        使用 Jackett Torznab API 根据关键字检索种子
        :param site:  站点
        :param keyword:  搜索关键词
        :param mtype:  媒体类型
        :param page:  页码
        :reutrn: 资源列表
        """
        results = []
        if not site or not keyword:
            return results

        if site.get("name", "").split("-")[0] != self.plugin_name:
            return results

        domain = StringUtils.get_url_domain(site.get("domain", ""))
        if not domain:
            logger.warning(f"【{self.plugin_name}】站点域名无法解析")
            return results

        indexer_name = domain.split(".")[-1]
        # V3 适配：不传 cat 分类参数。实测大量 Jackett indexer 分类映射不标准
        # （如 nyaa 动漫音乐映射到 150332/2020 等自定义分类），传 cat 会漏掉目标资源。
        # 由 MoviePilot 上层按标题/媒体类型做匹配过滤。

        try:
            logger.info(f"【{self.plugin_name}】开始检索 Indexer：\"{site.get('name')}\"，关键词：\"{keyword}\"")

            params = {
                "apikey": self._api_key,
                "t": "search",
                "q": keyword
            }
            query_string = urlencode(params, quote_via=quote_plus)
            api_url = f"{self._host.rstrip('/')}/api/v2.0/indexers/{indexer_name}/results/torznab/?{query_string}"

            result_array = self.__parse_torznab_xml(api_url, site, mtype)

            if not result_array:
                logger.warning(f"【{self.plugin_name}】Indexer：\"{site.get('name')}\" 未检索到数据")
                return results

            logger.info(f"【{self.plugin_name}】Indexer：\"{site.get('name')}\" 返回数据：{len(result_array)} 条")
            results.extend(result_array)

        except Exception as e:
            logger.error(f"【{self.plugin_name}】检索出错：{str(e)}")

        return results

    @staticmethod
    def get_cat(mtype: Optional[MediaType] = None):
        if not mtype:
            return [2000, 5000]
        elif mtype == MediaType.MOVIE:
            return [2000]
        elif mtype == MediaType.TV:
            return [5000]
        elif mtype == MediaType.MUSIC:
            # V3 适配：音乐分类 3000（原版缺失此分支，音乐搜索会传入影视分类导致音乐资源被过滤）
            return [3000]
        else:
            return [2000, 5000]

    def get_indexers(self, filter_selected: bool = True):
        """
        获取配置的 Jackett Indexer 信息
        :return: Indexer 列表，每项包含 id、name、url、domain、public、proxy、parser
        """
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": settings.USER_AGENT,
            "X-Api-Key": self._api_key,
            "Accept": "application/json, text/javascript, */*; q=0.01"
        }

        cookie = None
        session = requests.session()

        try:
            login_url = f"{self._host.rstrip('/')}/UI/Dashboard"
            login_data = {"password": self._password}
            login_params = {"password": self._password}
            login_res = RequestUtils(headers=headers, session=session).post_res(
                url=login_url,
                data=login_data,
                params=login_params,
                proxies=settings.PROXY if self._proxy else None
            )
            if login_res and session.cookies:
                cookie = session.cookies.get_dict()
            else:
                logger.warning(f"【{self.plugin_name}】Jackett 登录失败，无法获取 cookie")

            indexer_query_url = f"{self._host.rstrip('/')}/api/v2.0/indexers?configured=true"
            ret = RequestUtils(headers=headers, cookies=cookie).get_res(
                indexer_query_url,
                proxies=settings.PROXY if self._proxy else None
            )

            if not ret or not ret.json():
                logger.warning(f"【{self.plugin_name}】未获取到任何 indexer 配置")
                return []

            raw_indexers = ret.json()
            logger.info(f"【{self.plugin_name}】Jackett indexers: {[v.get('id') for v in raw_indexers]}")
            # 白名单过滤：勾选 indexer_sites 时仅保留选中的，留空添加全部
            selected = self._parse_indexer_sites()
            logger.info(f"【{self.plugin_name}】白名单原始配置: {self._indexer_sites!r}")
            if selected:
                logger.info(f"【{self.plugin_name}】白名单 selected: {selected}")
            indexers = []
            for v in raw_indexers:
                indexer_id = v.get("id")
                indexer_name = v.get("name")
                if not indexer_id or not indexer_name:
                    continue
                if filter_selected and selected and str(indexer_id).lower() not in selected \
                        and f"{self.plugin_name}-{indexer_name}".lower() not in selected:
                    logger.info(f"【{self.plugin_name}】白名单跳过 indexer: {indexer_id}")
                    continue

                # V3 适配：解析 Jackett caps 生成媒体类型分类。
                # V3 音乐搜索的站点列表依赖 indexer.category.music 字段，
                # 无 category 的索引器在音乐搜索中被过滤（电影/电视默认放行）。
                category = {}
                for cap in (v.get("caps") or []):
                    cap_id = str(cap.get("ID", ""))
                    if cap_id.startswith("2000"):
                        category["movie"] = True
                    elif cap_id.startswith("5000"):
                        category["tv"] = True
                    elif cap_id.startswith("3000"):
                        category["music"] = True

                indexers.append({
                    "id": f'{self.plugin_name}-{indexer_name}',
                    "indexer_id": indexer_id,
                    "name": f'{self.plugin_name}-{indexer_name}',
                    "url": f'{self._host.rstrip("/")}/api/v2.0/indexers/{indexer_id}/results/torznab/',
                    "domain": self.jackett_domain.replace(self.plugin_author, str(indexer_id)),
                    "public": True,
                    "proxy": False,
                    "category": category,
                })

            logger.info(f"【{self.plugin_name}】获取到 {len(indexers)} 个 Jackett indexers")
            return indexers

        except Exception as e:
            logger.error(f"【{self.plugin_name}】获取 Jackett indexers 失败：{str(e)}")
            return []

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

        return {
            "search_torrents": _wrapped_search,
            "async_search_torrents": _wrapped_search,
            "refresh_torrents": _wrapped_search,
            "async_refresh_torrents": _wrapped_search,
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

    def __parse_torznab_xml(self, url, site: dict = None, mtype: Optional[MediaType] = None) -> List[TorrentInfo]:
        """
        从 torznab XML 中解析种子信息
        :param url: XML 数据的 URL
        :return: TorrentInfo 列表
        """
        if not url:
            return []
        try:
            ret = RequestUtils(timeout=60).get_res(url,
                                                   proxies=settings.PROXY if self._proxy else None)
        except Exception as e:
            logger.error(str(e))
            return []
        if not ret or not ret.text:
            return []
        xmls = ret.text
        torrents = []
        try:
            # 解析XML
            dom_tree = xml.dom.minidom.parseString(xmls)
            root_node = dom_tree.documentElement
            items = root_node.getElementsByTagName("item")
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

                    torznab_attrs = item.getElementsByTagName("torznab:attr")
                    for torznab_attr in torznab_attrs:
                        name = torznab_attr.getAttribute('name')
                        value = torznab_attr.getAttribute('value')
                        if name == "seeders":
                            seeders = value
                        if name == "peers":
                            peers = value
                        if name == "downloadvolumefactor":
                            downloadvolumefactor = value
                            if float(downloadvolumefactor) == 0:
                                freeleech = True
                        if name == "uploadvolumefactor":
                            uploadvolumefactor = value
                        if name == "imdbid":
                            imdbid = value

                    tmp_dict = TorrentInfo(
                        title=title,
                        enclosure=enclosure,
                        description=description,
                        size=size,
                        seeders=seeders,
                        peers=peers,
                        # V3 适配：显示真实站点名（原版硬编码 jackett_domain 导致结果来源显示无意义域名）
                        site_name=site.get("name", self.plugin_name) if site else self.plugin_name,
                        page_url=page_url,
                        # V3 适配：V3 的 TorrentInfo 为 @dataclass，不接受未声明字段 imdbid
                        # V3 适配：填种子分类。MP 音乐匹配（_matching_music_torrents）要求
                        # torrent.category == MUSIC，原版不填导致音乐搜索全部被过滤。
                        # Jackett 的 torznab category 值（nyaa 150332/118685 等源 ID）与标准
                        # 分类不一致，无法可靠映射，直接用宿主搜索 mtype 兜底（音乐搜索时
                        # mtype 必为 MUSIC，再由上层标题+艺术家匹配筛除无关资源）。
                        category=mtype.value if mtype else None,
                    )
                    torrents.append(tmp_dict)
                except Exception as e:
                    logger.error(str(e))
                    continue
        except Exception as e:
            logger.error(f"检索错误：{traceback.format_exc()}")
            pass

        return torrents

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 动态生成索引器多选选项(完整列表,不受白名单过滤影响,否则无法取消勾选)
        site_options = []
        try:
            for idx in self.get_indexers(filter_selected=False):
                site_options.append({"title": f"{idx.get('name', '')} ({idx.get('indexer_id', '')})",
                                     "value": idx.get('indexer_id') or idx.get('id', '')})
        except Exception as e:
            logger.warning(f"【{self.plugin_name}】获取索引器选项失败: {str(e)}")
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
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
                                            'text': '该种方式扩建检索，无法进行站点连通性监测，官方默认方式添加的正常不影响！'
                                                    '日志出现报如下错误时，可以不用管，由于插件没有检索到数据会触发后续模块检索，导致错误'
                                                    'indexer - 【JackettExtend】ACG.RIP 搜索出错：NoneType object has no attribute get'
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
            "cron": "0 0 * * *"
        }

    def _ensure_sites_loaded(self) -> bool:
        """
        确保 self._indexers 已加载数据，若为空则尝试重新加载。
        :return: 成功加载返回 True，否则 False
        """
        if isinstance(self._indexers, list) and len(self._indexers) > 0:
            return True

        # 尝试重新加载站点数据
        self.get_status()

        return isinstance(self._indexers, list) and len(self._indexers) > 0

    def get_page(self) -> List[dict]:
        """
            拼装插件详情页面，需要返回页面配置，同时附带数据
        """
        if not self._ensure_sites_loaded():
            return []

        items = []
        for site in self._indexers:
            items.append({
                'component': 'tr',
                'content': [
                    {
                        'component': 'td',
                        'text': site.get("id")
                    },
                    {
                        'component': 'td',
                        'text': f"https://{site.get('domain')}"
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
