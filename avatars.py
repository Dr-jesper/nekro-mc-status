"""Small Java skin faces from Mojang's official endpoints, with safe defaults.

Only known official HTTPS hosts are requested. Texture URLs returned as HTTP by
Mojang are validated and upgraded to HTTPS. Redirects are never followed.
"""
import asyncio
import base64
import io
import json
import re
import time
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from uuid import UUID

import aiohttp
from PIL import Image, ImageDraw

from .models import PlayerInfo

MAX_BYTES = 262_144
MAX_JSON_BYTES = 65_536
_NAME = re.compile(r"[A-Za-z0-9_]{1,16}\Z")
_HASH = re.compile(r"/texture/([0-9a-fA-F]{32,64})\Z")
HttpFetch = Callable[[str, int], Awaitable[bytes]]


def _public_uuid(value: str | None) -> str | None:
    """Version 3 offline UUIDs cannot identify an official skin."""
    try:
        parsed = UUID(str(value))
        return parsed.hex if parsed.version == 4 else None
    except (ValueError, TypeError, AttributeError):
        return None


def texture_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or parsed.hostname != "textures.minecraft.net"
            or parsed.username or parsed.password or parsed.port is not None
            or parsed.query or parsed.fragment or not _HASH.fullmatch(parsed.path)):
        raise ValueError("Untrusted skin texture URL")
    return "https://textures.minecraft.net" + parsed.path


async def official_http_fetch(url: str, limit: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "api.mojang.com", "sessionserver.mojang.com", "textures.minecraft.net"
    } or parsed.username or parsed.password or parsed.port is not None:
        raise ValueError("Only official HTTPS endpoints are supported")
    timeout = aiohttp.ClientTimeout(total=2.5, connect=1.5)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200 or (response.content_length or 0) > limit:
                raise ValueError("Skin response unavailable or too large")
            data = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("Skin response too large")
            return bytes(data)


def default_face() -> Image.Image:
    """An explicit grey question-mark placeholder, never a claimed player skin."""
    face = Image.new("RGBA", (8, 8), (93, 94, 106, 255))
    draw = ImageDraw.Draw(face)
    draw.rectangle((0, 0, 7, 0), fill=(145, 146, 157))
    for x, y in ((2, 1), (3, 1), (4, 1), (5, 2), (4, 3), (3, 4), (3, 6)):
        draw.point((x, y), fill=(233, 232, 238))
    return face


def crop_skin(data: bytes) -> Image.Image:
    if len(data) > MAX_BYTES:
        raise ValueError("Skin too large")
    with Image.open(io.BytesIO(data)) as source:
        if source.format != "PNG" or source.size not in ((64, 64), (64, 32)):
            raise ValueError("Expected a Java 64x64 or 64x32 PNG skin")
        legacy = source.size == (64, 32)
        source.load()
        skin = source.convert("RGBA")
    face = skin.crop((8, 8, 16, 16))
    overlay = skin.crop((40, 8, 48, 16))
    # Java's legacy transparency hack tests the entire 32x32 outer region,
    # rather than just the 8x8 front hat. One alpha below 128 preserves hats;
    # only a wholly opaque/near-opaque region is treated as an unused block.
    outer_min_alpha = skin.crop((32, 0, 64, 32)).getchannel("A").getextrema()[0]
    if not legacy or outer_min_alpha < 128:
        face.alpha_composite(overlay)
    return face


class AvatarClient:
    """Bounded async fetcher with positive/negative TTL and injectable HTTP."""

    def __init__(self, fetch: HttpFetch = official_http_fetch, *, concurrency: int = 4,
                 timeout: float = 3.5, success_ttl: float = 3600,
                 failure_ttl: float = 90, cache_size: int = 512):
        self.fetch = fetch
        self.timeout = max(0.05, timeout)
        self.success_ttl, self.failure_ttl = success_ttl, failure_ttl
        self.cache_size = max(1, cache_size)
        self._cache: OrderedDict[str, tuple[float, Image.Image]] = OrderedDict()
        self._concurrency = max(1, min(8, concurrency))
        self._limiters = weakref.WeakKeyDictionary()
        self._pending = {}

    async def _resolve(self, player: PlayerInfo) -> Image.Image:
        identity = _public_uuid(player.uuid)
        if identity is None:
            # A safe official username lookup is allowed when the sample UUID
            # is absent/offline; arbitrary display names never enter a URL.
            if not _NAME.fullmatch(player.name):
                raise ValueError("No public Java identity")
            raw = await self.fetch("https://api.mojang.com/users/profiles/minecraft/" + player.name,
                                   MAX_JSON_BYTES)
            if len(raw) > MAX_JSON_BYTES:
                raise ValueError("Profile JSON too large")
            profile = json.loads(raw)
            if not isinstance(profile, dict) or str(profile.get("name", "")).lower() != player.name.lower():
                raise ValueError("Profile name does not match")
            identity = _public_uuid(profile.get("id"))
            if not identity:
                raise ValueError("Not an official public UUID")
        raw = await self.fetch("https://sessionserver.mojang.com/session/minecraft/profile/" + identity,
                               MAX_JSON_BYTES)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("Session JSON too large")
        profile = json.loads(raw)
        if not isinstance(profile, dict) or _public_uuid(profile.get("id")) != identity:
            raise ValueError("Session UUID does not match")
        properties = profile.get("properties", [])
        if not isinstance(properties, list) or len(properties) > 16:
            raise ValueError("Invalid properties")
        value = next(item["value"] for item in properties
                     if isinstance(item, dict) and item.get("name") == "textures")
        if not isinstance(value, str) or len(value) > MAX_JSON_BYTES:
            raise ValueError("Invalid texture property")
        decoded = json.loads(base64.b64decode(value, validate=True))
        if decoded.get("profileId", identity).replace("-", "").lower() != identity:
            raise ValueError("Texture identity does not match")
        url = texture_url(decoded["textures"]["SKIN"]["url"])
        return await asyncio.to_thread(crop_skin, await self.fetch(url, MAX_BYTES))

    async def face(self, player: PlayerInfo) -> Image.Image:
        identity = _public_uuid(player.uuid)
        key = "uuid:" + identity if identity else "name:" + player.name.lower()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            self._cache.move_to_end(key)
            return cached[1].copy()
        loop = asyncio.get_running_loop()
        pending_key = (loop, key)
        if pending_key in self._pending:
            return (await asyncio.shield(self._pending[pending_key])).copy()

        async def obtain():
            result, ttl = default_face(), self.failure_ttl
            limiter = self._limiters.setdefault(loop, asyncio.Semaphore(self._concurrency))
            try:
                async with asyncio.timeout(self.timeout):
                    async with limiter:
                        result = await self._resolve(player)
                        ttl = self.success_ttl
            except Exception:
                pass  # A missing skin is a presentation fallback, not query failure.
            self._cache[key] = (time.monotonic() + ttl, result.copy())
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
            return result

        task = asyncio.create_task(obtain())
        self._pending[pending_key] = task
        task.add_done_callback(lambda done: self._pending.pop(pending_key, None))
        # A caller's total query deadline must not cancel another caller's
        # shared skin request. The obtain task has its own strict deadline.
        return (await asyncio.shield(task)).copy()


default_client = AvatarClient()
