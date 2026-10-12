"""Nekro native Minecraft status plugin and admin-editable configuration."""
from typing import Literal

from pydantic import Field
from nekro_agent.api.plugin import ConfigBase, NekroPlugin

plugin = NekroPlugin(
    name="Nekro Minecraft Status", module_name="nekro_mc_status",
    description="公开查询温商及水专 Minecraft 服务器状态、在线玩家与头像",
    version="0.1.1", author="jesper", url="https://github.com/Dr-jesper/nekro-mc-status",
    allow_sleep=False,
)


@plugin.mount_config()
class MCStatusConfig(ConfigBase):
    ENABLED: bool = Field(default=True, title="启用状态查询")
    NATURAL_LANGUAGE_ENABLED: bool = Field(default=True, title="启用自然语言查询")
    GROUP_PROFILES: dict[str, str] = Field(default_factory=dict, title="群服务器绑定", description="完整 chat_key 对应 wzbc 或 shou；未绑定提示用 /wzbc 或 /shou")
    ALLOW_GROUPS: list[str] = Field(default_factory=list, title="允许频道", description="完整 chat_key；空列表允许所有频道")
    DENY_GROUPS: list[str] = Field(default_factory=list, title="禁止频道", description="完整 chat_key；优先于允许列表")
    WZBC_STYLE: Literal["wzbcmc", "rich", "simple"] = Field(default="wzbcmc", title="温商图片风格")
    SHOU_STYLE: Literal["shoumc", "rich", "simple"] = Field(default="shoumc", title="水专图片风格")
    DISPLAY_TIMEZONE: str = Field(default="Asia/Shanghai", title="查询时间时区", description="IANA 时区名称；默认北京时间")
    WZBC_SERVERS: list[dict[str, str]] = Field(default_factory=lambda: [
        {"name": "模组服", "host": "mc.wzbcmc.cn"},
        {"name": "校园复建服", "host": "build.wzbcmc.cn"},
    ], title="温商服务器", description="每项包含 name 和 host，最多 8 台")
    SHOU_SERVERS: list[dict[str, str]] = Field(default_factory=lambda: [
        {"name": "SMP生存", "host": "smp.shoumc.com"},
        {"name": "Create创造建筑", "host": "create.shoumc.com"},
        {"name": "SHOU建筑展示", "host": "shou.shoumc.com"},
    ], title="水专服务器", description="每项包含 name 和 host，最多 8 台")
    NATURAL_COOLDOWN_SECONDS: float = Field(default=20, ge=1, le=300, title="自然语言查询冷却秒数")
    COMMAND_COOLDOWN_SECONDS: float = Field(default=3, ge=1, le=60, title="显式命令冷却秒数")
    CACHE_SECONDS: float = Field(default=10, ge=0, le=60, title="状态缓存秒数")
    QUERY_TIMEOUT_SECONDS: float = Field(default=5, ge=1, le=15, title="单服查询时限秒数")
    REQUEST_TIMEOUT_SECONDS: float = Field(default=20, ge=3, le=60, title="查询出图总时限秒数")
    MAX_CONCURRENT_REQUESTS: int = Field(default=2, ge=1, le=8, title="全局并发上限")
    SHOW_AVATARS: bool = Field(default=True, title="显示 Minecraft 玩家头像")
    FONT_PATH: str = Field(default="", title="中文字体路径", description="留空使用打包字体")
    TEMP_RETENTION_SECONDS: int = Field(default=600, ge=60, le=3600, title="临时图片保留秒数")


mc_config: MCStatusConfig = plugin.get_config(MCStatusConfig)
