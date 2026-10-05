"""Public-page adapter for olevod.com."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from .registry import Detail, Episode, SearchResult, SourceAdapter, SourceError


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/120 Safari/537.36"
_DETAIL_RE = re.compile(r"/index\.php/vod/detail/id/(\d+)\.html", re.I)
_PLAY_RE = re.compile(
    r"<a\b[^>]*?href=[\"']([^\"']*/index\.php/vod/play/id/(\d+)/sid/(\d+)/nid/(\d+)\.html)[\"'][^>]*>(.*?)</a>",
    re.I | re.S,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_PLAYER_RE = re.compile(r"var\s+player_aaaa\s*=\s*(\{.*?\})\s*</script>", re.I | re.S)
_EPISODE_RE = re.compile(r"第\s*0*(\d+)\s*集", re.I)


def _text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split())


def _fetch(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except Exception as exc:  # urllib has several concrete network exceptions
        raise SourceError(f"olevod 请求失败：{url}：{exc}") from exc
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _fetch_json(url: str, timeout: int = 30) -> dict:
    text = _fetch(url, timeout=timeout)
    match = _PLAYER_RE.search(text)
    if not match:
        raise SourceError(f"olevod 播放页没有 player_aaaa 配置：{url}")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SourceError(f"olevod 播放配置不是有效 JSON：{url}：{exc}") from exc


def _title_from_page(page: str, fallback: str = "") -> str:
    match = _TITLE_RE.search(page)
    if not match:
        return fallback
    title = _text(match.group(1))
    title = re.split(r"\s+-\s*欧乐影院|欧乐影院", title, maxsplit=1)[0]
    title = re.sub(r"_(?:更新至|完结|第\s*\d+\s*集|高清播放|超清播放|高清|超清).*$", "", title)
    return title.strip(" _-") or fallback


def _episode_number(label: str) -> Optional[int]:
    match = _EPISODE_RE.search(label)
    return int(match.group(1)) if match else None


class OlevodAdapter(SourceAdapter):
    name = "olevod"
    base_url = "https://olevod.com"

    def search(self, query: str) -> List[SearchResult]:
        query = query.strip()
        if not query:
            raise SourceError("搜索词不能为空")
        url = f"{self.base_url}/index.php/vod/search.html?wd={quote(query)}"
        page = _fetch(url)
        results: List[SearchResult] = []
        seen: set[str] = set()
        pattern = re.compile(
            r"<a\b(?P<attrs>[^>]*\bhref=[\"'][^\"']*/index\.php/vod/detail/id/\d+\.html[\"'][^>]*)>"
            r"(?P<body>.*?)</a>",
            re.I | re.S,
        )
        for match in pattern.finditer(page):
            attrs = match.group("attrs")
            href_match = re.search(r"\bhref=[\"']([^\"']*/index\.php/vod/detail/id/(\d+)\.html)", attrs, re.I)
            if not href_match:
                continue
            href, item_id = href_match.groups()
            if item_id in seen:
                continue
            title_match = re.search(r"\btitle=[\"']([^\"']*)[\"']", attrs, re.I)
            # Search result cards have a title attribute; omit ranking/recommendation
            # anchors elsewhere on the same page.
            if not title_match:
                continue
            attr_title = title_match.group(1)
            body = match.group("body")
            title = _text(attr_title or body)
            if not title or title in {"立即播放", "播放"}:
                continue
            seen.add(item_id)
            results.append(
                SearchResult(
                    source=self.name,
                    title=title,
                    detail_url=urljoin(self.base_url, html.unescape(href)),
                    item_id=item_id,
                )
            )
        return results

    def get_detail(self, result: SearchResult) -> Detail:
        detail_url = result.detail_url
        page = _fetch(detail_url)
        title = _title_from_page(page, result.title)
        item_id = result.item_id or self._item_id(detail_url)
        episodes: List[Episode] = []
        seen: set[tuple[str, str]] = set()
        for match in _PLAY_RE.finditer(page):
            href, matched_id, sid, nid, body = match.groups()
            if item_id and matched_id != item_id:
                continue
            play_url = urljoin(self.base_url, html.unescape(href))
            label = _text(body)
            number = _episode_number(label)
            # Some links contain icon-only text; derive a stable label from nid.
            if number is None and len(episodes) > 0:
                number = int(nid)
            if number is not None and not label:
                label = f"第{number}集"
            key = (sid, nid)
            if key in seen:
                continue
            seen.add(key)
            episodes.append(
                Episode(number=number, label=label or f"第{nid}集", play_url=play_url, source=self.name, sid=sid)
            )
        episodes.sort(key=lambda item: (item.number is None, item.number or 0, item.play_url))
        if not episodes:
            raise SourceError(f"olevod 详情页没有播放条目：{detail_url}")
        media_type = "series" if any(item.number is not None for item in episodes) and len(episodes) > 1 else "movie"
        if media_type == "movie" and len(episodes) > 1:
            episodes = episodes[:1]
        return Detail(
            source=self.name,
            title=title,
            detail_url=detail_url,
            item_id=item_id,
            media_type=media_type,
            episodes=episodes,
            metadata={"year": self._year_from_page(page)},
        )

    def resolve_media_url(self, episode: Episode) -> str:
        config = _fetch_json(episode.play_url)
        url = config.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise SourceError(f"olevod 播放配置缺少公开媒体 URL：{episode.play_url}")
        return url

    @staticmethod
    def _item_id(url: str) -> str:
        match = _DETAIL_RE.search(url)
        return match.group(1) if match else ""

    @staticmethod
    def _year_from_page(page: str) -> Optional[str]:
        match = re.search(r"(?:年份|年)[：:/ ]{0,4}(20\d{2})", _text(page))
        return match.group(1) if match else None
