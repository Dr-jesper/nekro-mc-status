# 和回复判断插件一起使用

本插件接住查询后返回 `BLOCK_TRIGGER`，让普通聊天链停止触发模型，但保留用户消息。

Nekro Agent 2.4.0 收集所有插件回调的信号。如果另一个插件返回 `FORCE_TRIGGER`，它会覆盖 `BLOCK_TRIGGER`。因此判断插件应在调用模型、做 @ 消息强制触发之前检查 MC 是否承接了这条消息。

把下面的函数放到自己的判断插件里：

```python
def mc_status_claims(ctx, message):
    from nekro_agent.services.plugin.collector import plugin_collector

    mc = plugin_collector.loaded_plugins.get("jesper.nekro_mc_status")
    if mc is None or not mc.is_enabled:
        return False
    module = getattr(mc, "_module", None)
    claims = getattr(module, "claims_message", None)
    return bool(claims(ctx, message)) if callable(claims) else False
```

然后在判断回调的前置分支加入：

```python
if mc_status_claims(ctx, message):
    return MsgSignal.CONTINUE
```

`MsgSignal` 导入自 `nekro_agent.schemas.signal`。`claims_message` 不做网络请求、不改状态，不消费去重或冷却额度。忙、冷却与未绑定提示也算承接的消息；MC 插件未加载、禁用或消息不在范围内时返回 false。

这是针对 Nekro 2.4.0 的约定，`_module` 属于框架内部接口，升级后需核对。仅有 `BLOCK_TRIGGER` 不能保证与任意强制触发插件一起使用时只回复一次。
