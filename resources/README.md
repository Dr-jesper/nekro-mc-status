# 可移植资源与归属

本目录随独立 Nekro 插件分发，不引用本机 AstrBot 仓库或字体目录。

- `wzbcmc/`：用户授权的温商 Logo 与 Minecraft Java 官方位图标题字；文件原样沿袭本项目已有资源，来源、游戏资源归属与摘要见子目录 README/source.json。游戏资产不适用代码 AGPL 许可。
- `shoumc/`：原版未激活 Conduit；保留原文件与来源，顶部绘制可见主体 49×52，不添加发光、装饰或色彩变换。
- `default_icon.png`：沿袭上游插件的默认服务器图标，只用于服务器 favicon 缺失或无效时的占位。
- `unifont_all-17.0.05.hex`：GNU UniFont 17.0.05；遵循 GPLv2+ 和字体嵌入例外，或 SIL OFL 1.1 双许可。完整许可为 `UniFont-LICENSE.txt` 与 `UniFont-OFL-1.1.txt`，官方说明 https://unifoundry.com/unifont/index.html 。未改字形。
- `NotoSansSymbols2-Regular.ttf`：Noto Sans Symbols 2；SIL OFL 1.1，完整许可和版权归属在 `NotoSansSymbols2-LICENSE.txt`。未改字形。

随包提供 Sarasa UI SC Regular/Bold，来源为 Sarasa Gothic，SIL OFL 1.1；完整版权和许可见 `Sarasa-LICENSE.txt`。用户可用 `FONT_PATH` 选择已安装的字体；缺字时使用 UniFont 回退。不分发商业字体。

玩家头像不随包编造：仅按状态查询中实际姓名/UUID访问 Mojang 官方 profile/session/texture 接口，从 64×64 或 64×32 原版皮肤裁出 8×8 正脸并按 Java 语义叠加帽子层。拿不到官服皮肤时使用程序生成的灰色问号像素占位，不能代表该玩家的真实皮肤。
