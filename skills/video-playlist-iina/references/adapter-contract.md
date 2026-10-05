# 视频网站适配器接口

适配器负责把一个视频网站的页面结构转换为播放列表核心能够使用的统一对象。核心不应依赖某个网站的 HTML 类名或 URL 细节。

## 统一对象

### `SearchResult`

```python
SearchResult(
    source="olevod",
    title="兰香如故",
    detail_url="https://olevod.com/index.php/vod/detail/id/84001.html",
    item_id="84001",
    year="2026",
    media_type="series",  # series | movie | None
)
```

### `Detail`

```python
Detail(
    source="olevod",
    title="兰香如故",
    detail_url="...",
    item_id="84001",
    media_type="series",
    episodes=[Episode(...)],
    metadata={"year": "2026"},
)
```

### `Episode`

```python
Episode(
    number=13,
    label="第13集",
    play_url="https://olevod.com/index.php/vod/play/id/84001/sid/1/nid/13.html",
    source="olevod",
    sid="1",
)
```

电影也使用一个 `Episode(number=None, label="电影名", ...)` 作为单条播放项。

## 适配器必须提供

每个适配器应提供下列方法或等价行为：

```python
class SourceAdapter:
    name: str
    base_url: str

    def search(self, query: str) -> list[SearchResult]: ...
    def get_detail(self, result: SearchResult) -> Detail: ...
    def resolve_media_url(self, episode: Episode) -> str: ...
```

- `search` 只返回公开搜索结果，不猜测不存在的 URL。
- `get_detail` 负责判断电影/电视剧并提取可播放的剧集或单条播放项。
- `resolve_media_url` 只解析页面公开提供的播放配置；找不到或需要权限时抛出可读错误。
- 返回的媒体地址应是可以直接交给 IINA 的 HLS、MP4 或其他公开媒体 URL。

## 错误约定

使用 `SourceError` 或带来源名称的 `RuntimeError`，错误信息应包含：

- 来源名称。
- 失败阶段（搜索、详情、剧集列表或播放地址）。
- 页面 URL（如果已知）。
- 是否可以保留旧播放地址继续使用。

更新流程遇到单集错误时不得删除已有条目；新集无法解析时应跳过并在最终报告中列出。

## 注册新适配器

1. 在 `scripts/sources/` 新增 `<source>.py`。
2. 实现上述对象和方法。
3. 在 `scripts/sources/registry.py` 的优先级列表中注册。
4. 用详情页和播放页样本验证搜索、集数标签和媒体 URL。

不要把账号、Cookie、密钥或绕过验证码的逻辑写入适配器。
