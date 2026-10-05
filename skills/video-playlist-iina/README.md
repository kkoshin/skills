# video-playlist-iina

一个面向 Claude Code 的 skill：搜索公开视频网站，把电视剧或电影整理成带标题的 IINA M3U 播放列表，并支持增量更新。

## 功能

- 支持电视剧和电影。
- 内置欧乐影院（`olevod.com`）搜索与播放页解析。
- 电视剧可以从“已看到第 N 集”之后开始生成。
- 更新时保留已有集数，只补充新集，并在播放地址变化时更新对应条目。
- M3U 内嵌来源、剧目 ID、起始集等元数据，不创建旁车 JSON。
- 明确要求打开时，在 IINA 新窗口以 1.5 倍速播放。
- 通过适配器接口扩展其他视频网站。

## 使用

把本目录放入 Claude Code 的 skills 路径，或直接在支持 skill 的环境中引用本目录。建议显式调用：

```text
$video-playlist-iina 整理《兰香如故》，我看到第 12 集，接着生成播放列表
$video-playlist-iina 更新《兰香如故》的播放列表
$video-playlist-iina 在 IINA 新窗口打开这个播放列表
```

也可以直接运行脚本：

```bash
python3 scripts/video_playlist.py search "兰香如故"
python3 scripts/video_playlist.py create --query "兰香如故" --start-episode 13
python3 scripts/video_playlist.py update outputs/兰香如故_IINA.m3u
python3 scripts/open_iina.py outputs/兰香如故_IINA.m3u --speed 1.5
```

默认输出目录是调用目录下的 `outputs/`。使用 `--output` 可以指定文件路径。

## 目录

```text
video-playlist-iina/
├── SKILL.md
├── README.md
├── references/
│   └── adapter-contract.md
└── scripts/
    ├── video_playlist.py
    ├── open_iina.py
    └── sources/
        ├── __init__.py
        ├── registry.py
        └── olevod.py
```

## 限制

- 只处理公开、无需登录或绕过访问控制的页面。
- 播放地址由视频网站返回，可能因站点更新或 CDN 过期而失效。
- 其他视频网站需要按照适配器接口单独加入。
- 更新是按需执行的，不会在后台自动轮询。

## License

MIT，沿用仓库根目录许可证。
