# GPT KeepAlive

跑在 **Linux** 上的小工具：自动识别 ChatGPT 窗口是否在运行，管理一个可配置的倒计时（默认 5 小时），

时间一到自动向 ChatGPT 发送「继续」消息；启用 OCR 后还可以自动识别新的限额提示并开始计时。

## 原理：倒计时从哪里来

ChatGPT 的功能和用量限制会随套餐、平台、地区、灰度发布和工作区设置变化；ChatGPT Work 与 Codex
可能共享用量限制。请以当前客户端显示的用量和重置时间为准，不要把 README 中的默认 5 小时当成固定规则。
官方概览见 [Use ChatGPT](https://learn.chatgpt.com/docs/use-chatgpt)。

典型流程是：任务因限额中断 → 等界面显示的窗口重置 → 本工具到点自动发一条「继续」。是否能继续仍由
当前会话状态和账户用量决定。工具也可以用于任何「每 N 小时需要发送一次续接消息」的场景。

> 推荐用法：触发限额后看一眼
>
> **Settings → Usage 的重置时间**
>
> ，
> 执行 
>
> `python3 gpt_keepalive.py reset --until 21:30`
>
> （把 21:30 换成界面显示的时间），
> 计时就和界面显示的重置时刻对齐，比直接使用默认时长更准。



```
┌────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ ChatGPT 窗口 │──▶│ 轮询检测(30s) │──▶│ 可配置倒计时  │──▶│ 到点自动发送   │
│ 是否在运行   │    │ 窗口/OCR识别  │    │ (可配置)      │    │ 「继续」消息   │
└────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                                        │                      │
                                        └──── 重置计时，循环 ────┘
```

## 功能



| 功能         | 说明                                                                            |
| ---------- | ----------------------------------------------------------------------------- |
| 窗口识别       | 按窗口标题 / 类名关键词识别 ChatGPT（X11 用 wmctrl/xdotool，Wayland 用 swaymsg/hyprctl，关键词可配） |
| 可配置计时      | 从 `reset`/首次运行时刻起倒计时，默认 5 小时，可用界面显示的重置时间精确对齐                              |
| 到点自动续活     | 时间到自动聚焦 ChatGPT 窗口并把「继续」消息发进输入框回车                                             |
| 自动重试       | 窗口不在线会等待；聚焦或发送失败时不会误发到其他窗口，并会按间隔重试                                          |
| 可选自动启动     | 窗口等待超时后可用 `launch_command` 拉起 ChatGPT，并在启动后重新检测                              |
| 可选 OCR 识别  | 截图 + OCR 识别「已达限额」提示；每次提示只重置一次，提示消失后才能再次触发                                    |
| 系统通知       | 发送成功 / 失败通过 notify-send 弹通知                                                   |
| systemd 服务 | 登录图形会话后自动启动、崩溃自动重启                                                           |

## 快速开始

### 1. 环境准备（在 Linux 机器上）

**X11 桌面**（GNOME/XFCE/KDE 等）：



```
sudo apt install xdotool xclip wmctrl     # Debian/Ubuntu
# 或: sudo dnf install xdotool xclip wmctrl
# 或: sudo pacman -S xdotool xclip wmctrl
```

**Wayland 桌面**（Sway / Hyprland / 其他）：



```
sudo apt install wtype                    # 输入（推荐，支持中文）
# 或: sudo pacman -S wtype
# Sway 自带 swaymsg；Hyprland 自带 hyprctl
# 兜底方案: sudo apt install ydotool wl-clipboard  (需启动 ydotool 守护进程)
```

### 2. 检查并运行

本目录没有安装脚本，直接运行即可：

```
chmod +x gpt_keepalive.py
python3 gpt_keepalive.py check    # 先确认能识别到窗口
python3 gpt_keepalive.py run      # 前台运行（Ctrl+C 退出）
```

需要 systemd 用户服务时，创建 `~/.config/systemd/user/gpt-keepalive.service`：

```ini
[Unit]
Description=GPT KeepAlive
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /这里填写绝对路径/gpt_keepalive.py run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

将 `ExecStart` 改成脚本的真实绝对路径，然后执行：

```
systemctl --user daemon-reload
systemctl --user enable --now gpt-keepalive.service
journalctl --user -u gpt-keepalive.service -f
```

### 3. 日常使用



| 时机               | 操作                                                                  |
| ---------------- | ------------------------------------------------------------------- |
| 刚触发限额（推荐）        | 打开 ChatGPT 的 **Settings → Usage** 看重置时间，执行 `reset --until 21:30` 对齐 |
| 只知道剩余时长          | `python3 gpt_keepalive.py reset --remaining 4h30m`                  |
| 使用默认倒计时          | `python3 gpt_keepalive.py reset`（默认 5 小时，可在配置中修改）                    |
| 平时               | 守护进程自动跑，无需操作                                                        |
| 查看状态             | `python3 gpt_keepalive.py status`                                   |
| 演练（不真发消息）        | `python3 gpt_keepalive.py run --dry-run`                            |

常用命令：



```
# 按界面显示的重置时刻对齐（推荐）
python3 gpt_keepalive.py reset --until 21:30
# 或按剩余时长：支持 4h30m / 4.5h / 270m（分钟）
python3 gpt_keepalive.py reset --remaining 4h30m
# 自定义到点发送的续活消息
python3 gpt_keepalive.py reset --until 21:30 --message "继续刚才的任务，把剩余部分做完"
python3 gpt_keepalive.py run --dry-run          # 先演练一轮确认没问题
python3 gpt_keepalive.py run                    # 正式运行
python3 gpt_keepalive.py status                 # 看剩余时间/窗口状态
```

## 配置（config.json）



| 配置项                             | 默认值                              | 说明                                                         |
| ------------------------------- | -------------------------------- | ---------------------------------------------------------- |
| `window_keywords`               | `["ChatGPT","chatgpt","OpenAI"]` | 窗口标题 / 类名匹配关键词，改成本机客户端的实际标题                                |
| `timer_hours`                   | `5`                              | 计时时长（小时）                                                   |
| `continue_message`              | `请继续完成之前的任务`                     | 到点自动发送的消息，可改为你自己的续活提示词                                     |
| `check_interval_seconds`        | `30`                             | 轮询间隔                                                       |
| `max_wait_minutes_after_expiry` | `30`                             | 到点后窗口不在线最多等待多久                                             |
| `action_retry_seconds`          | `60`                             | 聚焦或发送失败后的重试间隔                                               |
| `window_detector`               | `auto`                           | `auto/x11/sway/hyprland/custom`                            |
| `input_backend`                 | `auto`                           | `auto/x11-paste/x11-type/wtype/ydotool-type/ydotool-paste` |
| `launch_command`                | 空                                | 窗口不在线且等待超时后执行的拉起命令                                         |
| `custom_window_check`           | 空                                | 自定义检测命令，stdout 每行视为一个窗口标题                                  |
| `custom_focus_command`          | 空                                | 自定义聚焦命令，支持 `{WINDOW_ID}` `{KEYWORD}`                       |
| `custom_input_command`          | 空                                | 自定义发送命令，支持 `{MSG}` `{WINDOW_ID}`                           |
| `notify`                        | `true`                           | 系统通知开关                                                     |
| `ocr_command`                   | 空                                | OCR 命令（见下）                                                 |

> 中文输入说明：X11 默认用 
>
> `x11-paste`
>
> （xclip 写入剪贴板 + Ctrl+V），对中文最稳；
> Wayland 默认用 
>
> `wtype`
>
> 。若你的环境特殊，可换成 
>
> `x11-type`
>
> /
>
> `ydotool-*`
>
>  或自定义命令。

## 完全自动化：OCR 自动识别限额提示（可选）

默认计时由 `reset` 手动启动。如果想让工具**自己识别**「达到限额」的提示并自动开始计时：



1. 安装截图和 OCR 工具。下面的 Sway 示例需要 `grim`、`jq`、`tesseract-ocr` 和中文语言包
   `tesseract-ocr-chi-sim`；X11 可改用 `import` 或 `gnome-screenshot` 截图。

2. 配置一个「截图 → OCR → 输出文本」的命令，例如（截取 ChatGPT 窗口区域）：



```
"ocr_command": "grim -g \"$(swaymsg -t get_tree | jq -r '.. | select(.app_id? == \"ChatGPT\") | .rect | \"\\(.x),\\(.y) \\(.width)x\\(.height)\"')\" - | tesseract stdin stdout -l chi_sim+eng",
"limit_pattern": "(reached your limit|已达.*上限|达到.*限额|usage limit)"
```



3. 守护进程每 `ocr_interval_seconds` 秒运行一次 OCR。限额提示从“未出现”变为“出现”时重置一次；
   提示持续留在屏幕上不会反复延后计时。

`ocr_command`、`custom_window_check` 和 `launch_command` 会通过本机 shell 执行，以支持管道、重定向和
命令替换，因此只能填写你信任的命令。OCR 截图可能包含
当前窗口中的对话内容；脚本自身不会上传截图或 OCR 文本，但第三方 OCR 命令是否联网取决于你配置的工具。

## 验证方法



```
python3 gpt_keepalive.py check          # 应输出「ChatGPT 窗口: 已检测到(N 个)」及窗口标题
python3 gpt_keepalive.py run --dry-run  # 到点后日志会显示 [演练] 将向 ChatGPT 发送: ...
python3 gpt_keepalive.py status         # 检查计时剩余时间正确递减
tail -f gpt-keepalive.log               # 查看守护进程日志
```

`--dry-run` 确认窗口识别、聚焦、消息内容都正确后，再正式 `run`。

## 常见问题

**Q：识别不到窗口？**

改 `window_keywords` 为本机客户端的实际窗口标题（`wmctrl -l` 或 `swaymsg -t get_tree` 查看真实标题）；

或用 `custom_window_check` 写自己的检测命令。

**Q：消息发出去了但 GPT 没收到 / 中文变乱码？**

X11 优先用 `input_backend: "x11-paste"`（剪贴板粘贴，对中文最稳）；确认 xclip 已安装。

Wayland 优先 `wtype`；用 ydotool 时需先启动 `ydotool` 守护进程并配好 uinput 权限。

**Q：发送时焦点跑到了别的窗口？**

先确认 ChatGPT 窗口存在且未最小化；X11 下 `xdotool windowactivate` 已自动聚焦目标窗口。

个别客户端屏蔽脚本输入时，可改用 `custom_input_command`（如模拟按键序列）。

**Q：重启后服务没跑？**

先运行 `systemctl --user status gpt-keepalive.service` 和
`journalctl --user -u gpt-keepalive.service -n 100` 查看错误。窗口自动化依赖图形会话，未登录或桌面未解锁时
通常无法检测、聚焦或输入，不建议为此启用 linger。

## 注意事项



* 本工具只是**等待官方窗口重置后自动续一条消息**，不绕过、不破解任何用量限制。

* 本工具无法改变套餐、工作区或模型的用量限制。若界面仍显示不可用，请以当前客户端给出的原因和重置时间为准。

* **Codex 云任务**被限额中断后，到点自动发送的消息会发在当前会话里；

  如需重新发起特定任务，把 `continue_message` 改成你的任务提示词即可。

* 请在 OpenAI 使用条款允许的范围内合理使用，避免对账号造成风险。

* 默认模式只发送配置的一条消息。启用 OCR 后会在本机截取并识别你配置的区域；脚本自身不上传这些内容。
