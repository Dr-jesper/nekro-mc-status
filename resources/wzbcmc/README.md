# WZBC MC 品牌资源

- `logo-symbol.png`：由温商 MC 社团提供的品牌 Logo，用于本模板。保留文件原样；渲染仅等比例缩小。社团为温州商学院 Minecraft 社团。
- `vanilla-font/ascii.png` 与 `default.json`：来自 Minecraft Java 1.21.4 官方客户端的原版默认位图字形及定义，只用于顶部 `WZBC MC`。按原定义匹配字形，用整数倍率及最近邻缩放绘制，填色沿用品牌紫色 `#601986`。下载来源、客户端 SHA1 与资源路径见 `vanilla-font/source.json`；这是 Mojang 的游戏资源，不是项目原创或 AGPL 字体。
- 中文与动态数据沿用插件自定义字体、系统 Noto 与内置 UniFont 回退，不打包 Windows 商业字体。

这些文件不是服务器状态数据或概念图背景。离线预览由 `examples/render_demo.py` 生成，使用模拟状态与合成皮肤。

服务器卡片的图标直接使用当前查询结果的 `icon_base64`；缺失或损坏时才回退原插件默认图标。顶部小 Logo 仍为社团品牌标识。
