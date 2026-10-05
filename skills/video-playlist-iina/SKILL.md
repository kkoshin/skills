---
name: video-playlist-iina
description: Use $video-playlist-iina when the user explicitly asks to search public video sites, create or update an IINA M3U playlist for a TV series or movie, or open that playlist in a new IINA window at a chosen speed. Supports episode ranges, incremental updates, and pluggable source adapters.
---

# 视频搜索与 IINA 播放列表

这个 skill 用于把公开视频网站的电视剧或电影整理成可在 IINA 中打开的 M3U 播放列表。默认支持欧乐影院，其他网站通过适配器加入。

## 工作流

1. **确认动作和输入**
   - 用户给出完整详情页 URL 时，跳过搜索。
   - 用户只给出剧名或电影名时，先搜索已配置的网站。
   - 识别内容类型：电视剧、电影；无法判断时先解析详情页。
   - 解析“看到第 N 集，接着看”为 `--start-episode N+1`。
   - 明确范围时传入 `--end-episode`；没有结束集数时覆盖当前已更新内容。

2. **搜索和选源**
   - 使用 `scripts/video_playlist.py search` 搜索。
   - 按 `scripts/sources/registry.py` 的优先顺序选择第一个精确匹配。
   - 同一来源有多个同名或模糊结果时，把候选标题、年份和详情页 URL 展示给用户，不要静默选择。
   - 将选中的来源和剧目 ID写入 M3U 注释，供后续更新使用。

3. **创建播放列表**
   - 使用 `scripts/video_playlist.py create`。
   - 默认输出到当前项目的 `outputs/<剧名>_IINA.m3u`；用户指定路径时使用用户路径。
   - 电视剧条目标题必须包含来源剧集标签，例如 `兰香如故 第13集`。
   - 电影默认只写入一个条目，选择第一个可访问的公开播放源。
   - 播放地址必须来自公开播放页的直接媒体配置，并通过 HLS 清单检查。

4. **增量更新**
   - 用户说“更新某剧”或“更新这个播放列表”时，优先读取已有 M3U 中的 `#CODEX-*` 注释。
   - 对没有注释的旧列表，从文件名、`#EXTINF` 标题和现有 URL 推断剧名与起始集；不确定时要求用户指定详情页或文件路径。
   - 使用 `scripts/video_playlist.py update`：
     - 保留已有集数和起始集。
     - 只加入新集，按集数排序。
     - 重新解析当前播放页；地址有变化时替换，旧地址仍可用时保留。
     - 新集解析失败时保留旧列表并报告失败集数，不删除已有条目。
   - 更新文件时使用临时文件再替换，避免中途失败破坏原列表。

5. **打开 IINA**
   - 创建或更新后不要自动打开播放器，除非用户明确要求。
   - 用户要求打开时运行：
     ```bash
     python3 scripts/open_iina.py <playlist.m3u> --speed 1.5
     ```
   - 脚本使用 IINA 的 `--separate-windows`、`--no-stdin` 和 `--mpv-speed=1.5`，只影响本次启动，不修改 IINA 全局设置。

6. **报告结果**
   - 报告剧名、来源、内容类型、集数范围、增加/更新/保留的条目数和输出文件绝对路径。
   - 如果来源不可访问、页面需要登录/验证码/DRM，说明原因并保留已有文件。

## M3U 元数据

脚本写入的 `#CODEX-*` 行都是 M3U 注释，播放器会忽略它们：

```text
#CODEX-PLAYLIST-VERSION=1
#CODEX-SOURCE=olevod
#CODEX-SOURCE-URL=https://olevod.com
#CODEX-TITLE=兰香如故
#CODEX-ID=84001
#CODEX-SID=1
#CODEX-TYPE=series
#CODEX-START-EPISODE=13
```

不要创建旁车 JSON。来源、剧目 ID 和起始集必须留在 M3U 内，以便用户移动文件后仍可更新。

## 适配器

- 当前内置 `olevod.com`，实现搜索、详情页集数提取和播放页 HLS 地址解析。
- 新网站应实现 [适配器接口](references/adapter-contract.md)，放入 `scripts/sources/`，再在 `registry.py` 中注册。
- 适配器只处理无需登录、验证码或 DRM 绕过的公开页面；不要为了提取地址绕过访问控制。

## 脚本入口

```bash
python3 scripts/video_playlist.py search "兰香如故"
python3 scripts/video_playlist.py create --query "兰香如故" --start-episode 13
python3 scripts/video_playlist.py create --detail-url 'https://olevod.com/index.php/vod/detail/id/84001.html' --start-episode 13
python3 scripts/video_playlist.py update outputs/兰香如故_IINA.m3u
python3 scripts/video_playlist.py validate outputs/兰香如故_IINA.m3u --network
python3 scripts/open_iina.py outputs/兰香如故_IINA.m3u --speed 1.5
```

所有脚本只使用 Python 标准库，不要安装第三方依赖。
