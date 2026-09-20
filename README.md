# pycfst

Cloudflare 优选 IP 测速的 Python 实现（仅 IPv4），功能对标 [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) 的核心流程。

**最大特点：单文件、零依赖（纯 Python 标准库），可在 iPhone 的 [a-Shell](https://holzschu.github.io/a-Shell_iOS/) 中直接下载运行**，无需电脑、无需 iSH 模拟器。

## 功能

- **延迟测速（TCPing）**：多线程对 Cloudflare 官方 IPv4 段抽样测 TCP 建连延迟（默认 443 端口），统计丢包率与平均延迟
- **条件过滤**：按延迟上下限（可过滤"假墙"IP）、丢包率筛选
- **下载测速**：TCP 直连 IP + SNI/Host 使用域名发起 HTTPS 下载（等效 `curl --resolve`），实测每个 IP 的下载速度，并从 `cf-ray` 响应头识别节点地区码（如 SJC、LAX）
- **结果输出**：终端打印 Top N 表格，完整结果写入 `result.csv`

## 在 iPhone（a-Shell）上使用

### 1. 安装 a-Shell

App Store 搜索 **a-Shell**（免费）并安装。

### 2. 下载脚本

打开 a-Shell，执行：

```sh
cd ~/Documents
curl -L -O https://raw.githubusercontent.com/gjgjgjgh/pycfst/main/cfst.py
```

国内网络无法访问 raw.githubusercontent.com 时，换用以下镜像之一：

```sh
# jsDelivr CDN（推荐）
curl -L -o cfst.py https://cdn.jsdelivr.net/gh/gjgjgjgh/pycfst@main/cfst.py

# jsDelivr 备用节点
curl -L -o cfst.py https://fastly.jsdelivr.net/gh/gjgjgjgh/pycfst@main/cfst.py
curl -L -o cfst.py https://gcore.jsdelivr.net/gh/gjgjgjgh/pycfst@main/cfst.py

# GitHub 代理镜像
curl -L -o cfst.py https://ghfast.top/https://raw.githubusercontent.com/gjgjgjgh/pycfst/main/cfst.py
```

（文件会出现在「文件」APP → 我的 iPhone → a-Shell 文件夹中）

### 3. 运行测速

```sh
# 只测延迟（最快，先跑这个看看网络环境）
python3 cfst.py -dd

# 完整测速：延迟 + 下载（推荐）
python3 cfst.py -tl 300 -dn 10

# 电信/联通用户过滤"假墙"IP（延迟低得反常的）
python3 cfst.py -tll 130

# 复测指定的几个 IP
python3 cfst.py -ip 104.16.1.1,172.67.5.5 -dn 2
```

如果提示找不到 `python3`，改用 `python cfst.py`。

### 4. 查看结果

终端会显示最快的 10 个 IP；完整结果在 `result.csv` 中，可用「文件」APP 打开 a-Shell 文件夹查看。

## 注意事项

- 测速全程**保持 a-Shell 在前台、屏幕常亮**（iOS 会挂起后台 APP）
- 测速前**关闭 Shadowrocket / Surge 等代理**，否则结果失真（延迟异常低就是走了代理或被"假墙"）
- 线程数 `-n` 建议 50~200，手机不要设太高
- 每次测速在每个 /24 段内随机抽样，两次结果不同属正常现象

## 参数说明

| 参数 | 默认值 | 说明 |
|---|---|---|
| `-n` | 100 | 延迟测速线程数 |
| `-t` | 4 | 单个 IP 延迟测速次数 |
| `-tp` | 443 | 测速端口 |
| `-timeout` | 1.0 | 单次连接超时（秒） |
| `-dn` | 10 | 下载测速数量 |
| `-dt` | 10 | 单个 IP 下载测速时长（秒） |
| `-url` | Cloudflare 官方测速地址 | 下载测速地址（必须是 Cloudflare CDN 上的大文件） |
| `-tl` | 9999 | 平均延迟上限（ms） |
| `-tll` | 0 | 平均延迟下限（ms），过滤假墙用 |
| `-tlr` | 1.00 | 丢包率上限（0.00~1.00，0 表示不容忍丢包） |
| `-sl` | 0 | 下载速度下限（MB/s） |
| `-p` | 10 | 终端显示结果数量，0 为不显示 |
| `-f` | 内置 CF IPv4 段 | 自定义 IP 段数据文件 |
| `-ip` | 空 | 直接指定 IP/段，逗号分隔 |
| `-o` | result.csv | 结果文件路径 |
| `-dd` | 关 | 禁用下载测速（结果按延迟排序） |
| `-allip` | 关 | 测速段内每个 IP（默认每 /24 随机 1 个） |
| `-debug` | 关 | 显示下载测速失败原因 |

## 与 XIU2/CloudflareSpeedTest 的差异

- 仅支持 IPv4
- 无 HTTPing 模式、无 `-cfcolo` 地区过滤（但下载测速会显示地区码）
- 默认下载测速地址为 Cloudflare 官方 `speed.cloudflare.com/__down`，可用 `-url` 换成任意自建 CF 测速地址

## 在其他平台

任何装有 Python 3.6+ 的设备（Windows / macOS / Linux / Android Termux）都可直接运行：

```sh
python3 cfst.py
```

## License

MIT
