"""Query/render/skin tests require no Nekro or AstrBot installation."""
import asyncio
import base64
import io
import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from nekro_mc_status import avatars, query
from nekro_mc_status.models import PlayerInfo, ServerSnapshot
from nekro_mc_status.graphics import get_img, shoumc, wzbcmc
from nekro_mc_status.graphics.players import prepare_rows
from nekro_mc_status.rendering import render_profile

UUID = "069a79f444e94726a5befca90e38aaf5"
HASH = "a" * 64


def png_skin(size=(64, 64), overlay=(0, 255, 0, 128)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (8, 8, 16, 16))
    image.paste(overlay, (40, 8, 48, 16))
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def profile_bytes(url=None):
    value = {"profileId": UUID, "textures": {"SKIN": {"url": url or "http://textures.minecraft.net/texture/" + HASH}}}
    return json.dumps({"id": UUID, "name": "Notch", "properties": [
        {"name": "textures", "value": base64.b64encode(json.dumps(value).encode()).decode()}
    ]}).encode()


def run(awaitable):
    return asyncio.run(awaitable)


def test_query_preserves_real_uuid_and_motd(monkeypatch):
    status = SimpleNamespace(players=SimpleNamespace(online=12, max=40, sample=[SimpleNamespace(name="Notch", id=UUID)]),
                             version=SimpleNamespace(name="1.21.4"), latency=30.4, icon=None,
                             motd={"text": "", "color": "#123456", "bold": True,
                                   "extra": [{"text": "Inherited"}, {"text": "Plain", "bold": False}]})
    async def lookup(host, timeout):
        assert host == "example.org" and timeout == 1
        async def status_call(version):
            return status
        return SimpleNamespace(async_status=status_call)
    monkeypatch.setattr(query.JavaServer, "async_lookup", lookup)
    snapshot = run(query.query_server("example.org", 1))
    assert snapshot.players == [PlayerInfo("Notch", UUID)]
    assert snapshot.online == 12 and snapshot.error is None
    assert snapshot.motd[0].segments[0].color == (18, 52, 86)
    assert snapshot.motd[0].segments[0].bold
    assert not snapshot.motd[0].segments[1].bold


def test_query_whole_deadline_and_unknown(monkeypatch):
    async def lookup(host, timeout):
        await asyncio.sleep(0.03)
        async def status_call(version):
            await asyncio.sleep(0.04)
        return SimpleNamespace(async_status=status_call)
    monkeypatch.setattr(query.JavaServer, "async_lookup", lookup)
    started = time.monotonic()
    result = run(query.query_server("example.org", 0.05))
    assert time.monotonic() - started < 0.3
    assert result.error == "查询超时" and result.online is None and result.players == []


def test_queries_bound_network_concurrency(monkeypatch):
    active, peak = 0, 0
    async def lookup(host, timeout):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        raise OSError("failed")
    monkeypatch.setattr(query.JavaServer, "async_lookup", lookup)
    async def exercise():
        return await asyncio.gather(*(query.query_server("example.org") for _ in range(12)))
    results = run(exercise())
    assert peak == 4
    assert all(s.online is None and s.error for s in results)


def test_motd_array_parent_and_sibling_style():
    lines = query.parse_motd([{"text": "A", "color": "#abc123", "bold": True},
                              {"text": "B", "bold": False}, {"text": "C\nD"}])
    assert [line.plain_text() for line in lines] == ["ABC", "D"]
    assert [s.bold for s in lines[0].segments] == [True, False, True]
    assert all(s.color == (171, 193, 35) for line in lines for s in line.segments)


@pytest.mark.parametrize("size", [(64, 64), (64, 32)])
def test_face_crop_alpha_overlay(size):
    face = avatars.crop_skin(png_skin(size))
    assert face.size == (8, 8)
    assert face.getpixel((0, 0)) == (127, 128, 0, 255)


def test_modern_opaque_hat_is_retained():
    face = avatars.crop_skin(png_skin(overlay=(0, 255, 0, 255)))
    assert face.getpixel((0, 0)) == (0, 255, 0, 255)


def test_legacy_opaque_front_hat_survives_transparent_outer_region():
    legacy = avatars.crop_skin(png_skin((64, 32), overlay=(0, 255, 0, 255)))
    assert legacy.getpixel((0, 0)) == (0, 255, 0, 255)


@pytest.mark.parametrize("outer_alpha, retained", [(255, False), (128, False), (127, True)])
def test_legacy_transparency_hack_checks_entire_outer_region(outer_alpha, retained):
    image = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (8, 8, 16, 16))
    image.paste((0, 255, 0, outer_alpha), (32, 0, 64, 32))
    image.paste((0, 255, 0, 255), (40, 8, 48, 16))
    data = io.BytesIO()
    image.save(data, "PNG")
    face = avatars.crop_skin(data.getvalue())
    assert face.getpixel((0, 0)) == ((0, 255, 0, 255) if retained else (255, 0, 0, 255))


@pytest.mark.parametrize("size", [(8, 8), (128, 128), (64, 128)])
def test_face_rejects_unexpected_dimensions(size):
    buffer = io.BytesIO()
    Image.new("RGBA", size).save(buffer, "PNG")
    with pytest.raises(ValueError):
        avatars.crop_skin(buffer.getvalue())


@pytest.mark.parametrize("url", ["https://127.0.0.1/texture/" + HASH,
    "https://textures.minecraft.net.evil.test/texture/" + HASH,
    "http://textures.minecraft.net:8080/texture/" + HASH,
    "https://user@textures.minecraft.net/texture/" + HASH,
    "https://textures.minecraft.net/texture/" + HASH + "?private=true",
    "https://textures.minecraft.net/texture/invalid"])
def test_texture_url_rejects_arbitrary_sources(url):
    with pytest.raises(ValueError):
        avatars.texture_url(url)


def test_avatar_official_url_upgrade_success_cache():
    calls = []
    async def fetch(url, limit):
        calls.append(url)
        return png_skin() if "textures.minecraft.net" in url else profile_bytes()
    client = avatars.AvatarClient(fetch)
    first = run(client.face(PlayerInfo("Notch", UUID)))
    second = run(client.face(PlayerInfo("Notch", UUID)))
    assert calls == ["https://sessionserver.mojang.com/session/minecraft/profile/" + UUID,
                     "https://textures.minecraft.net/texture/" + HASH]
    assert first.tobytes() == second.tobytes()


@pytest.mark.parametrize("raw", [b"bad-json", b'{}', b'{"id":"wrong"}',
    json.dumps({"id": UUID, "properties": [{"name": "textures", "value": "bad-base64"}]}).encode(),
    profile_bytes("https://127.0.0.1/texture/" + HASH), b"x" * 70000],
    ids=["bad-json", "empty", "wrong-id", "bad-base64", "private-url", "oversize"])
def test_avatar_bad_data_negative_cache(raw):
    calls = []
    async def fetch(url, limit):
        calls.append(url)
        return raw
    client = avatars.AvatarClient(fetch)
    assert run(client.face(PlayerInfo("Notch", UUID))).tobytes() == avatars.default_face().tobytes()
    run(client.face(PlayerInfo("Notch", UUID)))
    assert len(calls) == 1


def test_avatar_timeout_and_unsafe_name_default():
    calls = []
    async def fetch(url, limit):
        calls.append(url)
        await asyncio.sleep(1)
    client = avatars.AvatarClient(fetch, timeout=0.05)
    assert run(client.face(PlayerInfo("Notch", UUID))).tobytes() == avatars.default_face().tobytes()
    assert run(client.face(PlayerInfo("http://evil.test/"))).tobytes() == avatars.default_face().tobytes()
    assert len(calls) == 1


def test_offline_uuid_checks_name_against_official_profile():
    calls = []
    async def fetch(url, limit):
        calls.append(url)
        if "api.mojang.com" in url:
            return json.dumps({"id": UUID, "name": "different_user"}).encode()
        raise AssertionError("No texture should be requested")
    client = avatars.AvatarClient(fetch)
    result = run(client.face(PlayerInfo("OfflineName", "00000000-0000-3000-8000-000000000000")))
    assert result.tobytes() == avatars.default_face().tobytes() and len(calls) == 1


def test_avatar_shared_fetch_survives_one_caller_cancel():
    calls = []
    async def fetch(url, limit):
        calls.append(url)
        await asyncio.sleep(0.04)
        return png_skin() if "textures.minecraft.net" in url else profile_bytes()
    async def exercise():
        client = avatars.AvatarClient(fetch)
        owner = asyncio.create_task(client.face(PlayerInfo("Notch", UUID)))
        peer = asyncio.create_task(client.face(PlayerInfo("Notch", UUID)))
        await asyncio.sleep(0.01)
        owner.cancel()
        result = await asyncio.gather(owner, peer, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert isinstance(result[1], Image.Image)
    run(exercise())
    assert len(calls) == 2


def test_measured_player_layout_and_sample_omission():
    font = get_img.load_render_font_sync(18)
    draw = ImageDraw.Draw(Image.new("RGB", (940, 20)))
    names = [f"Player{i}_long_name" for i in range(12)]
    faces = {name: avatars.default_face() for name in names}
    rows = prepare_rows(draw, names, 999, font, 800, 2, faces)
    shown = sum(item["face"] is not None for row in rows for item in row["items"])
    text = "".join(s.text for row in rows for item in row["items"] for s in item["segments"])
    assert all(row["width"] <= 800 for row in rows)
    assert 0 < shown <= 8 and f"+{12 - shown}" in text
    assert "+999" not in text
    empty = prepare_rows(draw, [], 999, font, 800, 2, faces)
    assert "未公开玩家名单" in "".join(s.text for row in empty for item in row["items"] for s in item["segments"])


def test_duplicate_sample_names_keep_their_own_faces():
    font = get_img.load_render_font_sync(18)
    draw = ImageDraw.Draw(Image.new("RGB", (940, 20)))
    red = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
    blue = Image.new("RGBA", (8, 8), (0, 0, 255, 255))
    rows = prepare_rows(draw, ["SameName", "SameName"], 2, font, 800, 2, {0: red, 1: blue})
    shown = [item for row in rows for item in row["items"] if item["face"] is not None]
    assert shown[0]["face"].getpixel((0, 0)) == (255, 0, 0, 255)
    assert shown[1]["face"].getpixel((0, 0)) == (0, 0, 255, 255)
    assert shown[1]["x"] > shown[0]["x"] + 24


@pytest.mark.parametrize("profile", ["wzbcmc", "shoumc", "rich", "simple"])
def test_native_profiles_render_without_framework(tmp_path, profile):
    snapshot = ServerSnapshot("example.org", "Player server", [PlayerInfo("Notch", UUID)],
                              1, 40, "1.21.4", 36, motd=query.parse_motd({"text": "RGB MOTD", "color": "#abcdef"}))
    class LocalFaces:
        async def face(self, player):
            return avatars.crop_skin(png_skin())
    output = run(render_profile(profile, [snapshot, snapshot], datetime(2026, 10, 11, 12),
                                tmp_path / (profile + ".png"), avatar_client=LocalFaces()))
    with Image.open(output) as image:
        assert image.width == 940 and image.height > 400
        palette = {color for count, color in image.getcolors(image.width * image.height)}
        assert (127, 128, 0) in palette
        if profile == "shoumc":
            assert (93, 69, 62) in palette and (249, 239, 228) in palette


def test_note_takes_priority_and_does_not_request_skin(tmp_path):
    snapshot = ServerSnapshot("example.org", players=[PlayerInfo("Notch", UUID)],
                              online=1, max=20, note="维护说明")
    class NoFetch:
        async def face(self, player):
            raise AssertionError("Notes take priority")
    output = run(render_profile("shoumc", [snapshot], datetime(2026, 10, 11), tmp_path / "note.png", avatar_client=NoFetch()))
    assert output.exists()


def test_unknown_failure_card_has_no_online_or_offline_claim(tmp_path, monkeypatch):
    captured = []
    original = wzbcmc.render_card
    async def capture(*args, **kwargs):
        captured.append((args, kwargs))
        return await original(*args, **kwargs)
    monkeypatch.setattr(wzbcmc, "render_card", capture)
    output = run(render_profile("wzbcmc", [ServerSnapshot("example.org", error="查询超时")],
                                datetime(2026, 10, 11), tmp_path / "failed.png", show_avatars=False))
    assert output.exists() and captured[0][1]["status"] == "unknown"
    assert captured[0][0][3:5] == (None, None)
