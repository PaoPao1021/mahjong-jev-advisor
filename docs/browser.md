# 网页版使用指南

适用：在 Chrome / Edge 中运行雀魂的用户。四人和三人局均使用网页 Hook 获取真实牌局事件；三人局自动识别人数及拔北操作。

## 下载与启动

在项目 [Releases](https://github.com/PaoPao1021/mahjong-jev-advisor/releases) 选择名称包含 `browser` 的 ZIP：

| 设备 | 文件后缀 | 启动程序 |
| --- | --- | --- |
| 64 位 Windows（Intel / AMD） | `browser-windows-x64.zip` | `MahjongJevAdvisor-Web.exe` |
| Apple Silicon Mac | `browser-macos-arm64.zip` | `MahjongJevAdvisor-Web.app` |

完整解压后启动，不要在 ZIP 内运行；保留旁边的 `browser-extension` 文件夹。不需要安装 Python。Intel Mac 可使用源码方式。

源码方式：下载或检出包含双版本入口的代码，Windows 双击 `run_browser_windows.cmd`；macOS 双击 `run_browser_macos.command`，或在项目目录执行 `zsh run_browser_macos.command`。首次使用需安装 Python 3.12，脚本会安装依赖。

## 首次使用

1. 打开【Jev设置】，选择服务商，填写该平台的 API Key，点击测试连接后保存。
2. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`，开启开发者模式，选择“加载解压缩的扩展”，加载包内 `browser-extension` 目录。【桌】→【打开网页 Hook 扩展目录】可以定位该目录。
3. **刷新雀魂网页**，进入牌局。若未开始监测，点击【开始识别】。
4. 对照游戏核对手牌、人数及按钮，再查看建议；操作仍由你在游戏内执行。

网页 Hook 不需要先选择屏幕范围、校准区域或标注模板。四人和三人局无需安装不同扩展。当前截图示例可在仓库 README 查看。

服务商官网：[TypeSafe](https://typesafe.ai/)、[Vercel AI Gateway](https://vercel.com/ai-gateway)、[OpenRouter](https://openrouter.ai/)。各平台 Key 不通用；软件不附带 Key 或模型额度。【大模型设置】只用于可选的【深度解析】。

## 日常操作与问题排查

- 【暂停识别】暂停自动建议；【AI建议】重新请求当前已识别局面的建议。
- 无手牌：刷新网页，在【桌】→【查看网页 Hook 诊断】检查状态计数；“已连接”不等于已经取得牌局。
- 更新扩展：在浏览器扩展页点击“重新加载”，再刷新雀魂网页。
- 端口 8765 被占用：关闭多余的网页版顾问实例后重试。
- 超时、429 或 5xx：程序有限次数自动重试。Key 或账单错误需在所选服务商处理。
- 需要手动研究：使用【手动核对】输入四人或三人手牌。不能取得 Hook 数据时，四人局还可以尝试校准后的截图路径。

## 与 Steam 版并存

网页版沿用原版配置：Windows 为 `%APPDATA%\mahjong-jev-advisor`；macOS 为 `~/Library/Application Support/mahjong-jev-advisor`。旧版 v0.1.0 的 Key、校准和模板保留。

Steam 版使用另一配置目录，Key 需要分别填写。无需卸载旧版；请把不同 ZIP 解压到不同文件夹。此入口固定为浏览器模式，如需 Steam，请启动独立的 Steam 版。

## 验证范围

浏览器协议会随游戏更新变化；自动化测试不能保证每一局面的解析都正确。三麻点数结算与复杂副露仍有已知限制，建议需核对。

启动包未商业签名或 Apple 公证。系统拦截时先确认来源和 SHA256，再使用系统提供的“仍要运行”或“隐私与安全性 → 仍要打开”；不要关闭全局防护。API Key 在本机配置中明文保存，勿分享配置文件。在线建议会将自己的手牌和公开局况发送到所选服务。
