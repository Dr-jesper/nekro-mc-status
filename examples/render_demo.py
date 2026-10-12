"""Generate offline mock previews. No server, account or skin API is queried."""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
# The Git checkout can have any name; load the actual package initializer.
if "nekro_mc_status" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "nekro_mc_status", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)

from nekro_mc_status.avatars import AvatarClient
from nekro_mc_status.models import PlayerInfo, ServerSnapshot
from nekro_mc_status.query import parse_motd
from nekro_mc_status.rendering import render_profile


def mock_skin(index: int) -> bytes:
    """Hand-drawn demo pixels, explicitly not a real player's skin."""
    skin = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(skin)
    skin_colors = [(188, 132, 91), (222, 173, 136), (141, 113, 96)]
    hair = [(55, 34, 25), (177, 81, 31), (28, 52, 61)][index % 3]
    draw.rectangle((8, 8, 15, 15), fill=skin_colors[index % 3])
    draw.rectangle((8, 8, 15, 9), fill=hair)
    draw.rectangle((8, 10, 8, 12), fill=hair)
    draw.rectangle((15, 10, 15, 12), fill=hair)
    for x in (10, 13):
        draw.point((x, 12), fill=(245, 242, 225))
        draw.point((x + 1, 12), fill=(61, 84, 111))
    draw.rectangle((11, 14, 13, 14), fill=(99, 60, 45))
    data = io.BytesIO()
    skin.save(data, "PNG")
    return data.getvalue()


def mock_client() -> AvatarClient:
    ids = [f"1000000000004000800000000000000{i}" for i in range(3)]

    async def fetch(url: str, limit: int) -> bytes:
        if "/session/minecraft/profile/" in url:
            identity = url.rsplit("/", 1)[-1]
            index = ids.index(identity)
            texture = {"profileId": identity, "textures": {"SKIN": {
                "url": "https://textures.minecraft.net/texture/" + str(index) * 64
            }}}
            profile = {"id": identity, "properties": [{"name": "textures", "value":
                base64.b64encode(json.dumps(texture).encode()).decode()}]}
            return json.dumps(profile).encode()
        if "/texture/" in url:
            return mock_skin(int(url[-1]))
        raise ValueError("Demo only accepts synthetic fixture URLs")

    return AvatarClient(fetch=fetch)


def snapshots(profile: str) -> list[ServerSnapshot]:
    names = ([("模组服", "mc.wzbcmc.cn"), ("校园复建服", "build.wzbcmc.cn")]
             if profile == "wzbc" else [
                 ("SMP 生存服", "smp.shoumc.com"),
                 ("Create 创造建筑服", "create.shoumc.com"),
                 ("SHOU 建筑展示服", "shou.shoumc.com")])
    icon = "data:image/png;base64," + base64.b64encode(
        (ROOT / "resources/default_icon.png").read_bytes()).decode()
    players = [PlayerInfo(name, f"1000000000004000800000000000000{i}")
               for i, name in enumerate(["DemoAlex", "DemoBuilder", "DemoSteve"])]
    result = []
    for index, (name, host) in enumerate(names):
        motd = {"text": "", "extra": [
            {"text": "模拟预览 · ", "color": "#55cdda", "bold": True},
            {"text": "校园方块世界", "color": "#ffabd0", "bold": True},
            {"text": "\n欢迎加入 Minecraft 社团", "color": "#ffe577"}]}
        result.append(ServerSnapshot(host=host, server_name=name, icon=icon,
            online=[12, 3, 0][index], max=[80, 40, 30][index],
            version=["1.20.1 · Forge", "1.21.1 · Java", "1.21.4 · Java"][index],
            latency=[42, 76, 135][index], motd=parse_motd(motd),
            players=players if index < 2 else []))
    return result


async def main(output: Path):
    timestamp = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    client = mock_client()
    for style, data_profile in [("wzbc", "wzbc"), ("shou", "shou"),
                                ("rich", "wzbc"), ("simple", "wzbc")]:
        path = await render_profile(style, snapshots(data_profile), timestamp,
                                    output / f"{style}.png", avatar_client=client)
        print(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "demo-output")
    asyncio.run(main(parser.parse_args().output))
