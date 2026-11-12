# Steam 版使用指南（毫秒级只读协议直连）

适用：[Steam 雀魂客户端](https://store.steampowered.com/app/1329410/MahjongSoul/)。采用 **100% 纯只读 Liqi WebSocket 协议流捕获引擎**，在内存中实时解码雀魂二进制协议并重建 `GameState`，再调用 Jev 建议引擎。**不截图、不注入内存、不改包、不发包，100% 精准识别四人麻将与三人麻将（含拔北、赤五、暗杠/加杠、断线重连恢复），支持全屏、缩放、后台及窗口遮挡。**

## 下载与启动

在项目 [Releases](https://github.com/PaoPao1021/mahjong-jev-advisor/releases) 选择名称包含 `steam` 的 ZIP：

| 设备 | 文件后缀 | 启动程序 |
| --- | --- | --- |
| 64 位 Windows（Intel / AMD） | `steam-windows-x64.zip` | `MahjongJevAdvisor-Steam.exe` |
| Apple Silicon Mac | `steam-macos-arm64.zip` | `MahjongJevAdvisor-Steam.app` |

完整解压后启动，无需安装 Python。源码方式：Windows 双击 `run_steam_windows.cmd`；macOS 双击 `run_steam_macos.command`，或在项目目录执行 `zsh run_steam_macos.command`。源码首次启动需要 Python 3.12。

## 首次配置（仅需 1 分钟）

1. 启动 Steam 版顾问，打开底部 **【Jev设置】**，填写服务商对应的 API Key 并点击【测试连接 · 真实请求】确认连接成功。
2. 点击顶部标题栏 **【桌】→【Steam 连接与证书设置】**（或 **【一键安装 / 检查本地 CA 证书】**）：
   - **第一步：安装本地 CA 证书**
     点击对话框中的 **【一键安装 / 信任本地 CA 证书】**。Windows 会调用 `certutil -user -addstore Root` 安装至当前用户受信任根证书库；macOS 会调用 `security add-trusted-cert` 或打开钥匙串引导信任。
   - **第二步：选择捕获模式（二选一）**
     - **模式 A：OS 进程直连模式（`local:Jantama_MahjongSoul`，默认推荐）**
       基于底层透明进程捕获，自动拦截 `Jantama_MahjongSoul` / `雀魂麻將` 进程的 WebSocket 流量，无需配置任何代理。若系统提示需要管理员/网络扩展权限而未授予，引擎会自动平滑回退到模式 B。
     - **模式 B：本地只读代理模式（`127.0.0.1:10999`，免管理员权限）**
       无需管理员权限，通过以下任一方式将 Steam 雀魂客户端流量指向本地只读代理：
       1. **Steam 启动项（最简）**：在设置窗口点击【复制 Steam 启动项】，在 Steam 库右键『雀魂麻将』→ 属性 → 通用 → 启动选项，粘贴 `--proxy-server=http://127.0.0.1:10999`。
       2. **系统 PAC 自动代理脚本**：点击【复制系统 PAC 地址】（`http://127.0.0.1:8765/proxy.pac`），填入系统网络设置的“使用自动配置脚本”，仅分流雀魂域名，不影响其他软件。
       3. **Clash / Mihomo / Surge / Proxifier 分流**：点击【复制 Clash 分流规则】，将 `Jantama_MahjongSoul.exe` 及雀魂域名指向 `127.0.0.1:10999`。若同时使用加速器或代理，可在“上游代理链”填写上游地址（如 `http://127.0.0.1:7890`）。

## 进入对局与实时建议

1. 启动 Steam 雀魂并进入牌局（支持四人麻将、三人麻将、友人房、段位场及断线重连）。
2. 确保顾问底部处于 **【暂停识别】**（即正在运行）状态。
3. 进入牌局或每次摸牌/对手出牌时，顾问会在 **5ms 内** 自动更新手牌、摸牌、四家/三家牌河、副露、立直状态、宝牌指示牌与操作按钮，并自动请求 Jev 建议。
4. 可随时点击 **【桌】→【查看 Steam 实时通道诊断】** 查看当前活跃模式、WebSocket 连接数、已捕获数据帧数、已重建局面数和本家座位。

## 与网页版并存

Steam 版使用 `%APPDATA%\mahjong-jev-advisor-steam`（Windows）或 `~/Library/Application Support/mahjong-jev-advisor-steam`（macOS）；不会覆盖网页版配置。模型 Key 需要单独填写。不同版本解压到不同文件夹即可并存，无需卸载旧版。

官网：[TypeSafe](https://typesafe.ai/)、[Vercel AI Gateway](https://vercel.com/ai-gateway)、[OpenRouter](https://openrouter.ai/)。Key 不通用，模型服务可能收费。顾问只在内存中只读解码协议帧，识别出的手牌、公开局况和候选动作会发送给配置的在线服务。
