"""Offline SDK-shaped unit checks. Integration with the real SDK is separate."""
from __future__ import annotations

import asyncio
import copy
import importlib
import logging
import sys
import re
import subprocess
import types
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """Keep mocks local and isolated from rendering tests and the real SDK."""
    class FakePlugin:
        def __init__(self, **kwargs):
            self.is_enabled = True
            self.commands = []
        def mount_config(self):
            return lambda cls: cls
        def get_config(self, cls):
            return cls()
        def get_plugin_data_dir(self):
            return tmp_path
        def mount_command(self, **metadata):
            def decorate(func):
                self.commands.append((metadata, func))
                return func
            return decorate
        def mount_on_user_message(self):
            return lambda func: func
        def on_disabled(self):
            return lambda func: func

    modules = {}
    def module(name, **exports):
        mod = types.ModuleType(name)
        mod.__path__ = []
        mod.__dict__.update(exports)
        modules[name] = mod
        monkeypatch.setitem(sys.modules, name, mod)
        return mod
    signal = NS(CONTINUE=0, BLOCK_TRIGGER=1, FORCE_TRIGGER=-1)
    permissions = NS(PUBLIC="public")
    ctl = NS(success=lambda content: NS(status="success", content=content), failed=lambda content: NS(status="error", content=content))
    class ConfigBase:
        def __init__(self):
            for key in type(self).__annotations__:
                setattr(self, key, copy.deepcopy(getattr(type(self), key)))
    def fake_field(default=None, *, default_factory=None, **kwargs):
        return default_factory() if default_factory is not None else default
    module("pydantic", Field=fake_field)
    for name in ["nekro_agent", "nekro_agent.api", "nekro_agent.schemas", "nekro_agent.core", "nekro_agent.tools", "nekro_agent.services", "nekro_agent.services.command", "nekro_agent.services.plugin"]:
        module(name)
    module("nekro_agent.api.plugin", ConfigBase=ConfigBase, NekroPlugin=FakePlugin, CmdCtl=ctl, CommandExecutionContext=NS, CommandPermission=permissions)
    module("nekro_agent.api.schemas", AgentCtx=NS)
    module("nekro_agent.schemas.chat_message", ChatMessage=NS)
    module("nekro_agent.schemas.signal", MsgSignal=signal)
    module("nekro_agent.core.logger", get_sub_logger=lambda name: logging.getLogger(name))
    module("nekro_agent.core.os_env", USER_UPLOAD_DIR=tmp_path / "uploads")
    module("nekro_agent.tools.path_convertor",
           sanitize_chat_key_for_path=lambda value: re.sub(r"[^a-zA-Z0-9_.-]", "_", value) or "unknown",
           convert_filename_to_sandbox_upload_path=lambda name: PurePosixPath("/app/uploads") / Path(name).name)
    module("nekro_agent.services.command.schemas", CommandOutputSegment=lambda **kwargs: NS(**kwargs), CommandResponse=NS)
    sends = []
    async def send_image(*args, **kwargs):
        sends.append(("image", args, kwargs))
    async def send_text(*args, **kwargs):
        sends.append(("text", args, kwargs))
    message_api = module("nekro_agent.api.message", send_image=send_image, send_text=send_text)
    module("nekro_agent.services.plugin.collector", plugin_collector=NS(loaded_plugins={}))
    package = module("mc_runtime_case")
    package.__path__ = [str(ROOT)]
    for name in ["plugin", "intent", "service", "main"]:
        monkeypatch.delitem(sys.modules, "mc_runtime_case." + name, raising=False)
    main = importlib.import_module("mc_runtime_case.main")
    intent = importlib.import_module("mc_runtime_case.intent")
    service = importlib.import_module("mc_runtime_case.service")
    @dataclass
    class Snapshot:
        host: str
        server_name: str = ""
        online: int | None = 1
        max: int | None = 20
        error: str | None = None
        players: list = field(default_factory=list)
    async def query(host, timeout=5):
        return Snapshot(host)
    async def render(profile, snapshots, query_time, output_path, **kwargs):
        output_path.write_bytes(b"test-image")
        return output_path
    async def materialize(path):
        return "/app/uploads/" + Path(path).name
    module("mc_runtime_case.models", ServerSnapshot=Snapshot)
    module("mc_runtime_case.query", query_server=query)
    module("mc_runtime_case.rendering", render_profile=render)
    yield NS(main=main, intent=intent, service=service, config=main.mc_config, plugin=main.plugin,
             api=message_api, modules=modules, sends=sends, snapshot=Snapshot, query=query, render=render,
             ctx=NS(chat_key="qq-group_123", db_chat_channel=NS(is_active=True, observe_mode=False, chat_type="group"), fs=NS(mixed_forward_file=materialize)), data=tmp_path)
    # importlib adds children itself, so explicitly remove them after each test.
    for name in list(sys.modules):
        if name.startswith("mc_runtime_case."):
            sys.modules.pop(name, None)


def message(text, mid="m1", segments=None):
    return NS(content_text=text, content_data=segments or [], message_id=mid, chat_key="qq-group_123", chat_type="group", is_recalled=False)


@pytest.mark.parametrize("text,profile", [
    ("/wzbc", "wzbc"), ("/shou", "shou"), ("服务器里有人吗", "wzbc"),
    ("服务器现在有几个人？", "wzbc"), ("水专服务器在线吗", "shou"),
    ("看看温商服务器在线人数", "wzbc"), ("上海海洋大学服务器状态怎么样", "shou"),
])
def test_queries(runtime, text, profile):
    assert runtime.intent.parse_intent(message(text), "wzbc").profile == profile


@pytest.mark.parametrize("text", [
    "他说服务器里有人吗", "不要查服务器里有人吗", "不用查服务器状态", "‘服务器里有人吗’这句话",
    "服务器宕机历史", "昨天服务器里有人吗", "服务器里有人在讨论", "为什么服务器里没人",
    "别人问服务器里有人吗", "朋友说服务器里有人吗", "不需要问服务器在线吗", "其他学校服务器有人吗",
    "/wzbc help", "/shoumc", "/other", "!wzbc", "温商和水专服务器里有人吗", "上海大学服务器有人吗",
])
def test_negative_intents(runtime, text):
    assert runtime.intent.parse_intent(message(text), "wzbc") is None


def test_segments_and_binding(runtime):
    text_at = [NS(type="at", text="@bot"), NS(type="text", text="/wzbc")]
    assert runtime.intent.parse_intent(message("@bot /wzbc", segments=text_at)).explicit
    quoted = [NS(type="reference", text="old"), NS(type="text", text="服务器里有人吗")]
    assert runtime.intent.parse_intent(message("服务器里有人吗", segments=quoted), "wzbc") is None
    # Actual OneBot conversion keeps only text and stores the quoted message ID
    # in ext_data; it does not emit a ChatMessage reference segment.
    qq_reply = message("服务器里有人吗", segments=[NS(type="text", text="服务器里有人吗")])
    qq_reply.ext_data = {"ref_msg_id": "old-platform-message"}
    assert runtime.intent.parse_intent(qq_reply, "wzbc") is None
    assert not runtime.main.claims_message(runtime.ctx, qq_reply)
    qq_reply.ext_data = {"ref_msg_id": None}
    assert runtime.intent.parse_intent(qq_reply, "wzbc").profile == "wzbc"
    runtime.config.GROUP_PROFILES = {"qq-group_123": "shou", "other-group_123": "wzbc"}
    assert runtime.main._intent_for(runtime.ctx, message("服务器里有人吗")).profile == "shou"
    assert runtime.main._intent_for(runtime.ctx, message("温商服务器里有人吗")).profile == "wzbc"
    runtime.config.GROUP_PROFILES = {}
    assert runtime.main._intent_for(runtime.ctx, message("服务器里有人吗")).profile is None


def test_public_and_disabled_scopes(runtime):
    assert [(meta["name"], meta["permission"]) for meta, _ in runtime.plugin.commands] == [("wzbc", "public"), ("shou", "public")]
    query = message("服务器里有人吗")
    assert runtime.main.claims_message(runtime.ctx, query)
    runtime.ctx.db_chat_channel.observe_mode = True
    assert not runtime.main.claims_message(runtime.ctx, query)
    assert runtime.main.claims_message(runtime.ctx, message("/wzbc"))
    runtime.ctx.db_chat_channel.observe_mode = False
    runtime.ctx.db_chat_channel.is_active = False
    assert not runtime.main.claims_message(runtime.ctx, query)
    runtime.config.ENABLED = False
    assert not runtime.main.claims_message(runtime.ctx, message("/wzbc"))
    runtime.config.ENABLED = True
    runtime.config.DENY_GROUPS = ["qq-group_123"]
    assert not runtime.main.claims_message(runtime.ctx, message("/wzbc"))


@pytest.mark.asyncio
async def test_unbound_hint_and_duplicate(runtime):
    main = runtime.main
    assert await main.on_user_message(runtime.ctx, message("服务器里有人吗")) == 1
    assert "用 /wzbc" in runtime.sends[0][1][1]
    assert runtime.sends[0][2]["ref_msg_id"] == "m1"
    assert await main.on_user_message(runtime.ctx, message("服务器里有人吗")) == 1
    assert len(runtime.sends) == 1
    # The selection prompt never consumed a query; choosing immediately works.
    assert await main.on_user_message(runtime.ctx, message("/shou", "choice1")) == 1
    assert runtime.sends[-1][0] == "image"
    await main.on_disabled()


@pytest.mark.asyncio
async def test_image_native_and_send_failure(runtime):
    result = await runtime.main.wzbc_command(NS(chat_key="qq-group_native"))
    assert result.content[0].type == "image"
    assert Path(result.content[0].file_path).is_file()
    assert not runtime.sends  # native output is sent exactly once by the SDK
    assert await runtime.main.on_user_message(runtime.ctx, message("/wzbc")) == 1
    assert runtime.sends[0][1][1].startswith("/app/uploads/")
    runtime.sends.clear()
    async def fail(*args, **kwargs):
        raise RuntimeError("platform failure")
    runtime.api.send_image = fail
    failed_msg = message("/wzbc", "fail1")
    failed_msg.chat_key = "qq-group_sendfail"
    assert await runtime.main.on_user_message(runtime.ctx, failed_msg) == 1
    assert await runtime.main.on_user_message(runtime.ctx, failed_msg) == 1
    assert not runtime.sends
    await runtime.main.on_disabled()


@pytest.mark.asyncio
async def test_coalescing_cache_and_channel_busy(runtime):
    gate = asyncio.Event()
    calls = []
    async def query(host, timeout=5):
        calls.append(host)
        await gate.wait()
        return runtime.snapshot(host)
    runtime.config.WZBC_SERVERS = [{"name": "test", "host": "test.example"}]
    service = runtime.service.StatusService(runtime.config, lambda: runtime.data, query=query, render=runtime.render)
    first = asyncio.create_task(service.prepare("wzbc", "g1", "1"))
    second = asyncio.create_task(service.prepare("wzbc", "g2", "2"))
    for _ in range(5):
        await asyncio.sleep(0)
    busy = await service.prepare("wzbc", "g1", "3", explicit=True)
    assert "正在查询" in busy.text
    assert calls == ["test.example"]
    gate.set()
    replies = await asyncio.gather(first, second)
    assert all(reply.image_path.is_file() for reply in replies)
    cached = await service.prepare("wzbc", "g3", "4")
    assert cached.image_path and len(calls) == 1
    assert (await service.prepare("wzbc", "g3", "4")).duplicate
    assert "刚刚查询" in (await service.prepare("wzbc", "g3", "5", explicit=True)).text
    assert (await service.prepare("wzbc", "g3", "6")).duplicate
    await service.close()


@pytest.mark.asyncio
async def test_global_limits_and_temp_cleanup(runtime):
    active = peak = 0
    gate = asyncio.Event()
    async def query(host, timeout=5):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await gate.wait()
        active -= 1
        return runtime.snapshot(host)
    runtime.config.WZBC_SERVERS = [{"name": str(i), "host": f"test{i}.example"} for i in range(4)]
    service = runtime.service.StatusService(runtime.config, lambda: runtime.data, query=query, render=runtime.render)
    first = asyncio.create_task(service.prepare("wzbc", "g1", "1"))
    second = asyncio.create_task(service.prepare("wzbc", "g2", "2"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert peak == 2
    assert "正忙" in (await service.prepare("wzbc", "g3", "3", explicit=True)).text
    gate.set()
    replies = await asyncio.gather(first, second)
    assert peak == 2 and all(reply.image_path for reply in replies)
    assert all(reply.image_path.is_relative_to(runtime.data) for reply in replies)
    # Scheduler delay can be short in tests; production validates 60..3600 s.
    runtime.config.TEMP_RETENTION_SECONDS = 0
    orphan = runtime.data / "status-images" / "mc-status-cleanup.png"
    orphan.write_bytes(b"temporary")
    service._schedule_cleanup(orphan)
    for _ in range(3):
        await asyncio.sleep(0)
    assert not orphan.exists()
    await service.close()


@pytest.mark.asyncio
async def test_failures_partial_and_request_timeout(runtime):
    async def fail(host, timeout=5):
        return runtime.snapshot(host=host, online=None, error="connection refused")
    service = runtime.service.StatusService(runtime.config, lambda: runtime.data, query=fail)
    reply = await service.prepare("wzbc", "g1")
    assert "无法确认在线" in reply.text and "在线 0" not in reply.text and not reply.image_path
    await service.close()
    async def partial(host, timeout=5):
        return await fail(host) if host == "mc.wzbcmc.cn" else runtime.snapshot(host)
    seen = []
    async def broken_render(profile, snapshots, *args, **kwargs):
        seen.extend(snapshots)
        raise ValueError("font failure")
    service = runtime.service.StatusService(runtime.config, lambda: runtime.data, query=partial, render=broken_render)
    reply = await service.prepare("wzbc", "g2")
    assert len(seen) == 2 and seen[0].error
    assert "图片生成失败" in reply.text and "在线 1/20" in reply.text
    await service.close()
    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()
    service = runtime.service.StatusService(runtime.config, lambda: runtime.data, query=stalled)
    runtime.config.REQUEST_TIMEOUT_SECONDS = 0.02
    reply = await service.prepare("wzbc", "g3")
    assert "查询超时" in reply.text and not service._busy and service._active == 0
    await service.close()


def test_claims_handshake_is_pure_and_tracks_scopes(runtime):
    assert runtime.main.claims_message(runtime.ctx, message("/wzbc"))
    assert runtime.main.claims_message(runtime.ctx, message("服务器里有人吗"))
    assert runtime.main._service is None and not runtime.sends
    runtime.plugin.is_enabled = False
    assert not runtime.main.claims_message(runtime.ctx, message("/wzbc"))


def test_natural_queries_are_group_only(runtime):
    query = message("服务器里有人吗")
    for chat_type in ("private", "unknown"):
        query.chat_type = chat_type
        assert not runtime.main.claims_message(runtime.ctx, query)
        command = message("/wzbc")
        command.chat_type = chat_type
        assert runtime.main.claims_message(runtime.ctx, command)


def test_pure_imports_never_require_sdk_or_install():
    code = '''
import importlib, importlib.abc, importlib.util, sys
class NoSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("nekro_agent"):
            raise AssertionError("pure import attempted Nekro SDK: " + fullname)
sys.meta_path.insert(0, NoSDK())
spec = importlib.util.spec_from_file_location("nekro_mc_status", sys.argv[1], submodule_search_locations=[sys.argv[2]])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
for name in ("models", "intent", "service", "query", "avatars", "rendering"):
    importlib.import_module("nekro_mc_status." + name)
assert "plugin" not in package.__dict__
'''
    result = subprocess.run([sys.executable, "-c", code, str(ROOT / "__init__.py"), str(ROOT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_collector_hasattr_initializes_hooks_once(runtime, monkeypatch):
    spec = importlib.util.spec_from_file_location("mc_lazy_case", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, package)
    spec.loader.exec_module(package)
    deps = importlib.import_module("mc_lazy_case.deps")
    calls = []
    monkeypatch.setattr(deps, "verify_imports", lambda: calls.append("verified"))
    try:
        assert not calls and "plugin" not in package.__dict__
        assert hasattr(package, "plugin")  # exact collector entry contract
        assert len(package.plugin.commands) == 2
        assert package.main.plugin is package.plugin
        assert callable(package.claims_message)
        assert calls == ["verified"]
        assert hasattr(package, "plugin") and calls == ["verified"]
    finally:
        for name in list(sys.modules):
            if name.startswith("mc_lazy_case."):
                sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_image_copy_failure_keeps_real_text(runtime, monkeypatch):
    def fail_copy(*args):
        raise OSError("uploads unavailable")
    monkeypatch.setattr(runtime.main, "_publish_upload_copy", fail_copy)
    result = await runtime.main.wzbc_command(NS(chat_key="qq-group_native"))
    assert result.content and result.content[0].type == "text"
    assert "在线 1/20" in result.content[0].text
    assert await runtime.main.on_user_message(runtime.ctx, message("/shou")) == 1
    assert len(runtime.sends) == 1 and runtime.sends[0][0] == "text"
    assert "在线 1/20" in runtime.sends[0][1][1]
    await runtime.main.on_disabled()


@pytest.mark.asyncio
async def test_quiet_natural_refusals_do_not_consume_unadmitted_cooldown(runtime):
    runtime.config.MAX_CONCURRENT_REQUESTS = 1
    runtime.config.WZBC_SERVERS = [{"name": "test", "host": "test.example"}]
    gate = asyncio.Event()
    started = asyncio.Event()
    async def query(host, timeout=5):
        started.set()
        await gate.wait()
        return runtime.snapshot(host)
    service = runtime.service.StatusService(runtime.config, lambda: runtime.data, query=query, render=runtime.render)
    first = asyncio.create_task(service.prepare("wzbc", "g1", "1"))
    await started.wait()
    assert (await service.prepare("wzbc", "g1", "2")).duplicate  # channel busy
    assert (await service.prepare("wzbc", "g2", "3")).duplicate  # global busy
    assert "g2" not in service._last
    assert (await service.prepare("wzbc", "g2", "4", explicit=True)).text
    assert "g2" not in service._last
    gate.set()
    assert (await first).image_path
    assert (await service.prepare("wzbc", "g1", "5")).duplicate  # natural cooldown
    assert (await service.prepare("wzbc", "g2", "6")).image_path  # admission now succeeds
    await service.close()


@pytest.mark.asyncio
async def test_disable_cancels_pending_query_without_late_send(runtime):
    runtime.config.GROUP_PROFILES = {runtime.ctx.chat_key: "wzbc"}
    service = runtime.main._get_service()
    started = asyncio.Event()
    async def query(host, timeout=5):
        started.set()
        await asyncio.Event().wait()
    service.query = query
    pending = asyncio.create_task(runtime.main.on_user_message(runtime.ctx, message("服务器里有人吗")))
    await started.wait()
    runtime.plugin.is_enabled = False
    await runtime.main.on_disabled()
    assert await pending == 1
    assert not runtime.sends and service._active == 0 and not service._busy
    runtime.plugin.is_enabled = True
    assert (await service.prepare("wzbc", "fresh")).duplicate  # old service remains closed


@pytest.mark.asyncio
async def test_disable_and_reenable_drops_old_render_reply(runtime):
    service = runtime.main._get_service()
    started = asyncio.Event()
    async def render(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    service.render = render
    pending = asyncio.create_task(runtime.main.wzbc_command(NS(chat_key="qq-group_native")))
    await started.wait()
    runtime.plugin.is_enabled = False
    await runtime.main.on_disabled()
    runtime.plugin.is_enabled = True
    result = await pending
    assert result.status == "error" and not runtime.sends


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,style", [("wzbc", "rich"), ("wzbc", "simple"), ("shou", "rich"), ("shou", "simple")])
async def test_configured_styles_and_beijing_time(runtime, profile, style):
    setattr(runtime.config, "WZBC_STYLE" if profile == "wzbc" else "SHOU_STYLE", style)
    seen = []
    async def render(actual_style, snapshots, query_time, output_path, **kwargs):
        seen.append((actual_style, query_time))
        return await runtime.render(actual_style, snapshots, query_time, output_path, **kwargs)
    service = runtime.service.StatusService(runtime.config, lambda: runtime.data, query=runtime.query, render=render)
    assert (await service.prepare(profile, "g1")).image_path
    assert seen[0][0] == style and seen[0][1].utcoffset().total_seconds() == 8 * 3600
    runtime.config.DISPLAY_TIMEZONE = "UTC"
    assert (await service.prepare(profile, "g2")).image_path
    assert seen[-1][1].utcoffset().total_seconds() == 0
    runtime.config.DISPLAY_TIMEZONE = "Invalid/Missing"
    assert "时区配置无效" in (await service.prepare(profile, "g3")).text
    await service.close()
