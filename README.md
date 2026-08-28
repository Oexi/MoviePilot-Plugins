# MoviePilot Plugins

MoviePilot 插件仓库，当前仅提供 **MoviePilot V3** 插件。

## 当前插件

### JackettExtend

- **版本**：3.2.16
- **适用版本**：MoviePilot V3
- **功能**：
  - 同步 Jackett 中已配置的 indexer，并在 MoviePilot 中生成对应站点
  - 支持电影、电视剧和音乐资源搜索
  - 支持 Jackett 登录密码、代理和搜索超时配置
  - 支持 indexer 白名单和定时同步
  - 支持站点状态、索引器类型和搜索结果展示

## 安装与配置

1. 在 MoviePilot V3 的插件市场中添加仓库：
   `https://github.com/Oexi/MoviePilot-Plugins`
2. 刷新插件市场并安装 `JackettExtend`。
3. 打开插件配置，填写：
   - Jackett 地址
   - Jackett API Key
   - （可选）Jackett Web 登录密码
   - （可选）代理、搜索超时、indexer 白名单和同步周期
4. 保存配置。插件会同步 Jackett 的 indexer，并将可用站点交给 MoviePilot 管理。

Jackett 中应先完成 indexer 的添加和配置。站点同步完成后，可在 MoviePilot 的站点管理中查看和启用对应站点。

## 版本说明

- 本仓库仅维护 V3 实现，不再提供 MoviePilot V2 插件。
- V2 版 `ProwlarrExtend` 已移除；当前插件不提供 Prowlarr 适配。
- `jackett_extend.<indexer>` 是 V3 已使用的虚拟站点标识，升级时会保留该前缀以兼容已有站点数据。

## 目录结构

```text
plugins.v3/jackettextend/   # JackettExtend V3 插件
package.v3.json             # V3 插件市场信息
tests/                      # 自动化测试
```

## 免责声明

本项目及其插件仅供学习和交流使用。请遵守当地法律法规，并自行承担使用本项目产生的责任。请勿将 API Key、密码或其他凭据提交到仓库、日志或公开渠道。

## 许可证

本项目采用 GPL-3.0-or-later，详见 [LICENSE](LICENSE)。
