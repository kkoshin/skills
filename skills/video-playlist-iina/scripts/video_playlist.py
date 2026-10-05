#!/usr/bin/env python3
"""Search public video sites and create/update IINA M3U playlists."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:  # Running as ``python scripts/video_playlist.py``.
    from sources.registry import Detail, Episode, SearchResult, SourceError, adapters, get_adapter
except ImportError:  # Running as a package or imported by a test.
    from .sources.registry import Detail, Episode, SearchResult, SourceError, adapters, get_adapter


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/120 Safari/537.36"
META_PREFIX = "#CODEX-"
EPISODE_RE = re.compile(r"第\s*0*(\d+)\s*集", re.I)
ALT_EPISODE_RE = re.compile(r"(?:episode|ep\.?|e)\s*0*(\d+)", re.I)


class PlaylistError(RuntimeError):
    """A user-actionable playlist error."""


@dataclass
class PlaylistEntry:
    label: str
    url: str
    number: Optional[int] = None


@dataclass
class Playlist:
    path: Path
    metadata: Dict[str, str]
    entries: List[PlaylistEntry]


def normalize_title(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def episode_number(value: str) -> Optional[int]:
    match = EPISODE_RE.search(value) or ALT_EPISODE_RE.search(value)
    return int(match.group(1)) if match else None


def clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s*第\s*0*\d+\s*集\s*$", "", value, flags=re.I)
    return value.strip(" _-")


def safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" .")
    return value or "playlist"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _request_text(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except Exception as exc:
        raise PlaylistError(f"读取页面失败：{url}：{exc}") from exc
    return raw.decode("utf-8", errors="replace")


def validate_media_url(url: str, timeout: int = 20) -> Tuple[bool, str]:
    """Fetch a small part of a public media URL and verify it looks playable."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-2047"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(2048)
            content_type = response.headers.get("content-type", "")
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (URLError, OSError, TimeoutError) as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)
    if not (200 <= status < 400):
        return False, f"HTTP {status}"
    if b"#EXTM3U" in body or "mpegurl" in content_type.lower() or url.lower().endswith((".mp4", ".mkv", ".mov")):
        return True, f"HTTP {status}"
    return False, f"响应不像媒体清单（{content_type or '未知类型'}）"


def _metadata_items(metadata: Dict[str, str]) -> Iterable[Tuple[str, str]]:
    preferred = [
        "PLAYLIST-VERSION",
        "SOURCE",
        "SOURCE-URL",
        "DETAIL-URL",
        "TITLE",
        "ID",
        "SID",
        "TYPE",
        "START-EPISODE",
        "END-EPISODE",
        "LAST-SYNC",
    ]
    emitted = set()
    for key in preferred:
        if key in metadata:
            emitted.add(key)
            yield key, metadata[key]
    for key in sorted(metadata):
        if key not in emitted:
            yield key, metadata[key]


def parse_m3u(path: Path) -> Playlist:
    if not path.is_file():
        raise PlaylistError(f"播放列表不存在：{path}")
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise PlaylistError(f"播放列表不是 UTF-8 文本：{path}") from exc
    metadata: Dict[str, str] = {}
    entries: List[PlaylistEntry] = []
    pending_extinf: Optional[str] = None
    for raw in lines:
        line = raw.strip()
        if line.startswith(META_PREFIX) and "=" in line:
            key, value = line[len(META_PREFIX) :].split("=", 1)
            metadata[key.strip()] = value.strip()
            continue
        if line.upper().startswith("#EXTINF"):
            pending_extinf = line
            continue
        if pending_extinf and line and not line.startswith("#"):
            label = pending_extinf.split(",", 1)[1].strip() if "," in pending_extinf else Path(line).name
            entries.append(PlaylistEntry(label=label, url=line, number=episode_number(label)))
            pending_extinf = None
    return Playlist(path=path, metadata=metadata, entries=entries)


def _entry_key(entry: PlaylistEntry) -> Tuple[str, str]:
    if entry.number is not None:
        return "episode", str(entry.number)
    return "label", normalize_title(entry.label)


def sort_entries(entries: Iterable[PlaylistEntry]) -> List[PlaylistEntry]:
    return sorted(entries, key=lambda entry: (entry.number is None, entry.number or 0, entry.label, entry.url))


def write_m3u(path: Path, metadata: Dict[str, str], entries: Iterable[PlaylistEntry]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_meta = {str(key): str(value).replace("\n", " ").replace("\r", " ") for key, value in metadata.items() if value is not None}
    safe_meta.setdefault("PLAYLIST-VERSION", "1")
    lines = ["#EXTM3U"]
    for key, value in _metadata_items(safe_meta):
        lines.append(f"{META_PREFIX}{key}={value}")
    for entry in sort_entries(entries):
        label = entry.label.replace("\n", " ").replace("\r", " ").replace('"', "'")
        group = safe_meta.get("TITLE", "视频").replace('"', "'")
        lines.append(f'#EXTINF:-1 tvg-name="{label}" group-title="{group}",{label}')
        lines.append(entry.url)
    content = "\n".join(lines) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def title_from_playlist(playlist: Playlist) -> str:
    if playlist.metadata.get("TITLE"):
        return clean_title(playlist.metadata["TITLE"])
    if playlist.entries:
        title = clean_title(playlist.entries[0].label)
        if title:
            return title
    stem = playlist.path.stem
    stem = re.sub(r"_IINA$", "", stem, flags=re.I)
    stem = re.sub(r"_第\s*\d+(?:-\d+)?\s*集$", "", stem, flags=re.I)
    return clean_title(stem)


def _source_for_url(url: str, requested: Optional[str] = None):
    if requested:
        return get_adapter(requested)
    host = (urlparse(url).hostname or "").lower()
    for adapter in adapters():
        base_host = (urlparse(adapter.base_url).hostname or "").lower()
        if host == base_host or host.endswith("." + base_host):
            return adapter
    raise PlaylistError(f"没有适配器支持这个 URL：{url}")


def _detail_url_for_input(url: str, adapter) -> str:
    parsed = urlparse(url)
    if "/vod/detail/" in parsed.path:
        return url
    match = re.search(r"/vod/play(?:_vip)?/id/(\d+)/", parsed.path, re.I)
    if match and adapter.name == "olevod":
        return f"{adapter.base_url}/index.php/vod/detail/id/{match.group(1)}.html"
    return url


def _search_results(query: str, source: Optional[str] = None) -> Tuple[List[SearchResult], List[str]]:
    chosen = [get_adapter(source)] if source else adapters()
    results: List[SearchResult] = []
    errors: List[str] = []
    for adapter in chosen:
        try:
            results.extend(adapter.search(query))
        except Exception as exc:
            errors.append(f"{adapter.name}: {exc}")
    return results, errors


def _format_candidates(results: Sequence[SearchResult]) -> str:
    return "\n".join(
        f"- [{result.source}] {result.title}"
        f"{f'（{result.year}）' if result.year else ''}: {result.detail_url}"
        for result in results
    )


def choose_result(query: str, results: Sequence[SearchResult]) -> SearchResult:
    normalized = normalize_title(query)
    exact = [result for result in results if normalize_title(result.title) == normalized]
    if not exact:
        fuzzy = [result for result in results if normalized and normalized in normalize_title(result.title)]
        if len(fuzzy) == 1:
            return fuzzy[0]
        candidates = fuzzy or list(results)
        raise PlaylistError(f"没有唯一精确匹配“{query}”，请从候选中指定详情页：\n{_format_candidates(candidates)}")
    source_order = {adapter.name: index for index, adapter in enumerate(adapters())}
    exact = sorted(exact, key=lambda result: source_order.get(result.source, 999))
    first_source = exact[0].source
    same_source = [result for result in exact if result.source == first_source]
    if len(same_source) > 1:
        raise PlaylistError(f"“{query}”在 {first_source} 有多个精确匹配，请指定详情页：\n{_format_candidates(same_source)}")
    return exact[0]


def _resolve_detail(query: Optional[str], detail_url: Optional[str], source: Optional[str]) -> Tuple[Any, Detail]:
    if detail_url:
        adapter = _source_for_url(detail_url, source)
        normalized_url = _detail_url_for_input(detail_url, adapter)
        item_id_match = re.search(r"/detail/id/(\d+)", normalized_url)
        result = SearchResult(
            source=adapter.name,
            title=query or "",
            detail_url=normalized_url,
            item_id=item_id_match.group(1) if item_id_match else "",
        )
        return adapter, adapter.get_detail(result)
    if not query:
        raise PlaylistError("必须提供 --query 或 --detail-url")
    results, errors = _search_results(query, source)
    if not results:
        suffix = f"；来源错误：{'；'.join(errors)}" if errors else ""
        raise PlaylistError(f"没有找到“{query}”{suffix}")
    result = choose_result(query, results)
    adapter = get_adapter(result.source)
    return adapter, adapter.get_detail(result)


def _resolve_many(adapter, episodes: Sequence[Episode], check_network: bool) -> Tuple[Dict[int, str], Dict[int, str]]:
    resolved: Dict[int, str] = {}
    errors: Dict[int, str] = {}

    def resolve_one(index_and_episode: Tuple[int, Episode]) -> Tuple[int, Optional[str], Optional[str]]:
        index, episode = index_and_episode
        try:
            url = adapter.resolve_media_url(episode)
            if check_network:
                ok, reason = validate_media_url(url)
                if not ok:
                    raise PlaylistError(f"媒体地址不可访问：{reason}")
            return index, url, None
        except Exception as exc:
            return index, None, str(exc)

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(episodes)))) as pool:
        futures = [pool.submit(resolve_one, item) for item in enumerate(episodes)]
        for future in as_completed(futures):
            index, url, error = future.result()
            if error:
                errors[index] = error
            elif url:
                resolved[index] = url
    return resolved, errors


def _selected_episodes(detail: Detail, start: Optional[int], end: Optional[int]) -> List[Episode]:
    if detail.media_type == "movie":
        return detail.episodes[:1]
    lower = start or 1
    selected = [episode for episode in detail.episodes if episode.number is not None and episode.number >= lower]
    if end is not None:
        selected = [episode for episode in selected if episode.number is not None and episode.number <= end]
    return selected


def _metadata_for_detail(adapter, detail: Detail, start: Optional[int], end: Optional[int]) -> Dict[str, str]:
    first_sid = detail.episodes[0].sid if detail.episodes else "1"
    metadata = {
        "PLAYLIST-VERSION": "1",
        "SOURCE": adapter.name,
        "SOURCE-URL": adapter.base_url,
        "DETAIL-URL": detail.detail_url,
        "TITLE": detail.title,
        "ID": detail.item_id,
        "SID": first_sid,
        "TYPE": detail.media_type,
        "LAST-SYNC": now_iso(),
    }
    if start is not None and detail.media_type == "series":
        metadata["START-EPISODE"] = str(start)
    if end is not None and detail.media_type == "series":
        metadata["END-EPISODE"] = str(end)
    return metadata


def _output_path(title: str, requested: Optional[str]) -> Path:
    return Path(requested).expanduser().resolve() if requested else (Path.cwd() / "outputs" / f"{safe_filename(title)}_IINA.m3u")


def command_search(args: argparse.Namespace) -> int:
    results, errors = _search_results(args.query, args.source)
    if args.json:
        print(json.dumps([result.__dict__ for result in results], ensure_ascii=False, indent=2))
    else:
        if results:
            print(_format_candidates(results))
        else:
            print(f"没有找到“{args.query}”。")
        if errors:
            print("来源错误：" + "；".join(errors), file=sys.stderr)
    return 0 if results else 1


def command_create(args: argparse.Namespace) -> int:
    adapter, detail = _resolve_detail(args.query, args.detail_url, args.source)
    if args.type != "auto":
        detail.media_type = args.type
    start = args.start_episode if detail.media_type == "series" else None
    end = args.end_episode if detail.media_type == "series" else None
    if start is not None and start < 1:
        raise PlaylistError("--start-episode 必须大于 0")
    if end is not None and start is not None and end < start:
        raise PlaylistError("--end-episode 不能小于 --start-episode")
    selected = _selected_episodes(detail, start, end)
    if not selected:
        raise PlaylistError(f"没有可生成的播放条目：{detail.title}")
    resolved, errors = _resolve_many(adapter, selected, not args.skip_network_check)
    entries: List[PlaylistEntry] = []
    for index, episode in enumerate(selected):
        if index not in resolved:
            continue
        label = detail.title if detail.media_type == "movie" else f"{detail.title} {episode.label}"
        entries.append(PlaylistEntry(label=label, url=resolved[index], number=episode.number))
    if not entries:
        raise PlaylistError(f"没有任何播放地址解析成功：{detail.title}")
    output = _output_path(detail.title, args.output)
    if output.exists() and not args.force:
        raise PlaylistError(f"文件已存在：{output}；如需覆盖请加 --force")
    metadata = _metadata_for_detail(adapter, detail, start, end)
    write_m3u(output, metadata, entries)
    print(f"已生成：{output}")
    print(f"来源：{adapter.name}；类型：{detail.media_type}；条目：{len(entries)}")
    if errors:
        failed_labels = [selected[index].label for index in errors if index < len(selected)]
        print(f"未能解析：{', '.join(failed_labels) or '部分条目'}", file=sys.stderr)
    return 0


def _legacy_detail(playlist: Playlist, source: Optional[str]) -> Tuple[Any, Detail]:
    title = title_from_playlist(playlist)
    if not title:
        raise PlaylistError("无法从旧播放列表推断剧名，请使用 --source 并提供详情页重新生成")
    detail_url = playlist.metadata.get("DETAIL-URL")
    if detail_url:
        return _resolve_detail(title, detail_url, source or playlist.metadata.get("SOURCE"))
    return _resolve_detail(title, None, source or playlist.metadata.get("SOURCE"))


def command_update(args: argparse.Namespace) -> int:
    playlist = parse_m3u(Path(args.playlist).expanduser().resolve())
    source_name = args.source or playlist.metadata.get("SOURCE")
    adapter, detail = _legacy_detail(playlist, source_name)
    if args.type != "auto":
        detail.media_type = args.type
    existing_by_key = {_entry_key(entry): entry for entry in playlist.entries}
    numbers = [entry.number for entry in playlist.entries if entry.number is not None]
    start = int(playlist.metadata.get("START-EPISODE", min(numbers) if numbers else 1))
    end_value = playlist.metadata.get("END-EPISODE")
    end = int(end_value) if end_value else None
    selected = _selected_episodes(detail, start if detail.media_type == "series" else None, end)
    resolved, errors = _resolve_many(adapter, selected, not args.skip_network_check)
    updated_entries: Dict[Tuple[str, str], PlaylistEntry] = dict(existing_by_key)
    added = 0
    changed = 0
    kept = 0
    for index, episode in enumerate(selected):
        key = ("episode", str(episode.number)) if episode.number is not None else ("label", normalize_title(detail.title))
        old = updated_entries.get(key)
        if index in resolved:
            label = detail.title if detail.media_type == "movie" else f"{detail.title} {episode.label}"
            new_entry = PlaylistEntry(label=label, url=resolved[index], number=episode.number)
            if old is None:
                added += 1
            elif old.url != new_entry.url or old.label != new_entry.label:
                changed += 1
            else:
                kept += 1
            updated_entries[key] = new_entry
        elif old is not None:
            kept += 1
    metadata = dict(playlist.metadata)
    metadata.update(_metadata_for_detail(adapter, detail, start if detail.media_type == "series" else None, end))
    write_m3u(playlist.path, metadata, updated_entries.values())
    print(f"已更新：{playlist.path}")
    print(f"来源：{adapter.name}；新增：{added}；地址/标题更新：{changed}；保留：{kept}")
    if errors:
        failed = [selected[index].label for index in errors if index < len(selected)]
        print(f"解析失败但未删除旧条目：{', '.join(failed) or '部分条目'}", file=sys.stderr)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    playlist = parse_m3u(Path(args.playlist).expanduser().resolve())
    duplicate_keys = len(playlist.entries) - len({_entry_key(entry) for entry in playlist.entries})
    print(f"条目数：{len(playlist.entries)}")
    print(f"重复集数：{duplicate_keys}")
    if not args.network:
        return 1 if duplicate_keys else 0
    failures = []
    for entry in playlist.entries:
        ok, reason = validate_media_url(entry.url)
        if not ok:
            failures.append(f"{entry.label}: {reason}")
    if failures:
        print("不可访问条目：", file=sys.stderr)
        print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
        return 1
    print("所有播放地址均返回可识别的媒体清单。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="搜索已配置的视频网站")
    search.add_argument("query")
    search.add_argument("--source")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_search)

    create = sub.add_parser("create", help="创建 M3U 播放列表")
    target = create.add_mutually_exclusive_group(required=True)
    target.add_argument("--query")
    target.add_argument("--detail-url")
    create.add_argument("--source")
    create.add_argument("--type", choices=["auto", "series", "movie"], default="auto")
    create.add_argument("--start-episode", type=int)
    create.add_argument("--end-episode", type=int)
    create.add_argument("--output")
    create.add_argument("--force", action="store_true")
    create.add_argument("--skip-network-check", action="store_true")
    create.set_defaults(func=command_create)

    update = sub.add_parser("update", help="增量更新已有 M3U 播放列表")
    update.add_argument("playlist")
    update.add_argument("--source")
    update.add_argument("--type", choices=["auto", "series", "movie"], default="auto")
    update.add_argument("--skip-network-check", action="store_true")
    update.set_defaults(func=command_update)

    validate = sub.add_parser("validate", help="检查 M3U 条目和媒体地址")
    validate.add_argument("playlist")
    validate.add_argument("--network", action="store_true")
    validate.set_defaults(func=command_validate)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (PlaylistError, SourceError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
