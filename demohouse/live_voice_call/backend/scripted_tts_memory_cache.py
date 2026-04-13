from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import List, Optional, Tuple


DEFAULT_SCRIPTED_TTS_MEM_CACHE_TTL_SECONDS = 1800
DEFAULT_SCRIPTED_TTS_MEM_CACHE_MAX_ENTRIES = 2000


def _load_ttl_seconds() -> int:
    raw_value = (os.getenv("INTERVIEW_TTS_MEM_CACHE_TTL_SECONDS") or "").strip()
    if not raw_value:
        return DEFAULT_SCRIPTED_TTS_MEM_CACHE_TTL_SECONDS
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_SCRIPTED_TTS_MEM_CACHE_TTL_SECONDS
    return max(1, parsed)


def _load_max_entries() -> int:
    raw_value = (os.getenv("INTERVIEW_TTS_MEM_CACHE_MAX_ENTRIES") or "").strip()
    if not raw_value:
        return DEFAULT_SCRIPTED_TTS_MEM_CACHE_MAX_ENTRIES
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_SCRIPTED_TTS_MEM_CACHE_MAX_ENTRIES
    return max(1, parsed)


@dataclass(frozen=True)
class CachedTTSSentence:
    transcript: str
    audio: bytes


@dataclass(frozen=True)
class _ScriptedTTSCacheItem:
    sentences: Tuple[CachedTTSSentence, ...]
    expires_at_mono: float


class ScriptedTTSMemoryCache:
    def __init__(self, *, ttl_seconds: Optional[int] = None, max_entries: Optional[int] = None):
        self.ttl_seconds = max(1, int(ttl_seconds or _load_ttl_seconds()))
        self.max_entries = max(1, int(max_entries or _load_max_entries()))
        self._store: "OrderedDict[Tuple[str, str, str], _ScriptedTTSCacheItem]" = OrderedDict()
        self._lock = Lock()

    def _build_key(self, token: str, speaker: str, text: str) -> Optional[Tuple[str, str, str]]:
        normalized_token = (token or "").strip()
        normalized_text = (text or "").strip()
        if not normalized_token or not normalized_text:
            return None
        normalized_speaker = (speaker or "").strip()
        return normalized_token, normalized_speaker, normalized_text

    def _prune_locked(self, now_mono: Optional[float] = None) -> None:
        now = now_mono if now_mono is not None else time.monotonic()
        expired_keys = [
            key
            for key, item in self._store.items()
            if item.expires_at_mono <= now
        ]
        for key in expired_keys:
            self._store.pop(key, None)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)

    def get(self, token: str, speaker: str, text: str) -> Optional[List[CachedTTSSentence]]:
        key = self._build_key(token, speaker, text)
        if key is None:
            return None
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            if item.expires_at_mono <= now:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return [CachedTTSSentence(transcript=s.transcript, audio=bytes(s.audio)) for s in item.sentences]

    def set(
        self,
        token: str,
        speaker: str,
        text: str,
        sentences: List[CachedTTSSentence],
    ) -> bool:
        key = self._build_key(token, speaker, text)
        if key is None:
            return False

        normalized_sentences: List[CachedTTSSentence] = []
        for sentence in sentences:
            audio = bytes(sentence.audio or b"")
            if not audio:
                continue
            normalized_sentences.append(
                CachedTTSSentence(transcript=str(sentence.transcript or ""), audio=audio)
            )

        if not normalized_sentences:
            return False

        expires_at = time.monotonic() + self.ttl_seconds
        with self._lock:
            self._store[key] = _ScriptedTTSCacheItem(
                sentences=tuple(normalized_sentences),
                expires_at_mono=expires_at,
            )
            self._store.move_to_end(key)
            self._prune_locked()
        return True

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


SCRIPTED_TTS_MEMORY_CACHE = ScriptedTTSMemoryCache()
