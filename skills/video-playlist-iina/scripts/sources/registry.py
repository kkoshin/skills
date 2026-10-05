"""Source adapter registry.

The registry is deliberately small: add a source adapter here when a new
public website is supported. The playlist core only talks to this interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol


class SourceError(RuntimeError):
    """A public-source lookup or parsing failure."""


@dataclass
class SearchResult:
    source: str
    title: str
    detail_url: str
    item_id: str = ""
    year: Optional[str] = None
    media_type: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Episode:
    number: Optional[int]
    label: str
    play_url: str
    source: str
    sid: str = "1"
    media_url: Optional[str] = None


@dataclass
class Detail:
    source: str
    title: str
    detail_url: str
    item_id: str
    media_type: str
    episodes: List[Episode]
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(Protocol):
    name: str
    base_url: str

    def search(self, query: str) -> List[SearchResult]: ...

    def get_detail(self, result: SearchResult) -> Detail: ...

    def resolve_media_url(self, episode: Episode) -> str: ...


def adapters() -> List[SourceAdapter]:
    # Imported lazily so this module remains the single registry entry point.
    from .olevod import OlevodAdapter

    return [OlevodAdapter()]


def get_adapter(name: str) -> SourceAdapter:
    wanted = name.strip().lower()
    for adapter in adapters():
        if adapter.name.lower() == wanted:
            return adapter
    raise SourceError(f"未注册的视频网站适配器：{name}")
