#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPT KeepAlive
=============
跑在 Linux 上的小工具：检测 ChatGPT 窗口是否在运行，自动管理可配置倒计时，
时间一到自动把「继续」消息发送给 ChatGPT，让它接着干活。

用量规则可能随套餐、平台、地区和工作区变化。本工具不推断账户规则；请按客户端显示的
重置时间使用 --until，或自行设置倒计时时长。到点后工具只负责尝试发送一条续接消息。

依赖：仅 Python 3 标准库。系统命令按桌面环境选择：
  X11     : xdotool + xclip（+ 可选 wmctrl）
  Wayland : wtype（推荐）或 ydotool + wl-copy；窗口查询用 swaymsg / hyprctl

子命令：
  check            检测 ChatGPT 窗口是否在运行
  reset            从现在开始一个新的计时（刚触发限额/任务中断时执行）
  run              守护进程：轮询窗口 + 计时 + 到点自动发「继续」消息
  status           查看剩余时间 / 窗口状态 / 轮次

示例：
  python3 gpt_keepalive.py check
  python3 gpt_keepalive.py reset
  python3 gpt_keepalive.py reset --until 21:30       # 对齐界面 Settings→Usage 显示的重置时间
  python3 gpt_keepalive.py reset --remaining 4h30m   # 或直接给剩余时长
  python3 gpt_keepalive.py run --dry-run             # 演练：只记录，不真正发送
  python3 gpt_keepalive.py run
  python3 gpt_keepalive.py status
"""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    # 窗口标题/类名中包含这些关键词即视为 ChatGPT（可改成你客户端的实际标题）
    "window_keywords": ["ChatGPT", "chatgpt", "OpenAI"],
    # 默认计时时长（小时）；实际使用时应以客户端显示的重置时间为准
    "timer_hours": 5,
    # 到点后自动发送的「继续」消息
    "continue_message": "请继续完成之前的任务",
    # 轮询间隔（秒）
    "check_interval_seconds": 30,
    # 到点后窗口一直不在线时，最多等待多少分钟才跳过本轮
    "max_wait_minutes_after_expiry": 30,
    # 聚焦或发送失败后的重试间隔（秒）
    "action_retry_seconds": 60,
    # 窗口检测方式：auto / x11 / sway / hyprland / custom
    "window_detector": "auto",
    # 输入发送方式：auto / x11-paste / x11-type / wtype / ydotool-type / ydotool-paste
    "input_backend": "auto",
    # 可选：窗口不在线且等待超时后，用该命令把 ChatGPT 拉起来（留空则不启动）
    "launch_command": "",
    # 可选：自定义窗口检测命令，stdout 每行视为一个命中窗口标题
    "custom_window_check": "",
    # 可选：自定义聚焦命令，支持 {WINDOW_ID} {KEYWORD}
    "custom_focus_command": "",
    # 可选：自定义发送命令，支持 {MSG} {WINDOW_ID}，将完全替代内置发送逻辑
    "custom_input_command": "",
    # 可选：发送完成/出错时用 notify-send 弹系统通知
    "notify": True,
    "notify_command": "notify-send",
    # 可选：OCR 自动识别限额提示。填一个截图+识别的命令（如 grim+tesseract），
    # 输出命中 limit_pattern 时自动重置计时。留空则关闭（默认关闭）
    "ocr_command": "",
    "ocr_interval_seconds": 60,
    "limit_pattern": r"(reached your limit|已达.*上限|达到.*限额|usage limit)",
    # 日志与状态文件（留空则放在脚本同目录）
    "log_file": "",
    "state_file": "",
}

_log_fp = None


# ---------------------------------------------------------------- 基础工具

def log(cfg, msg):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg
    print(line, flush=True)
    global _log_fp
    try:
        if _log_fp is None:
            _log_fp = open(cfg["log_file"], "a", encoding="utf-8")
        _log_fp.write(line + "\n")
        _log_fp.flush()
    except Exception as e:
        print("(无法写日志文件 %s: %s)" % (cfg["log_file"], e), flush=True)


def run_cmd(cmd):
    """执行命令（cmd 为参数列表，不经 shell，避免注入）。返回 (returncode, stdout)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        output = p.stdout.strip()
        if p.returncode != 0 and p.stderr.strip():
            output = p.stderr.strip()
        return p.returncode, output
    except FileNotFoundError:
        return -1, ""
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def run_cmd_stdin(cmd, data):
    """执行命令并把 data 作为 stdin 传入。返回 (returncode, stdout)。"""
    try:
        p = subprocess.run(cmd, input=data.encode("utf-8"), capture_output=True, timeout=20)
        stdout = p.stdout.decode("utf-8", "ignore").strip()
        stderr = p.stderr.decode("utf-8", "ignore").strip()
        return p.returncode, stderr if p.returncode != 0 and stderr else stdout
    except FileNotFoundError:
        return -1, ""
    except Exception as e:
        return -1, str(e)


def tool_available(name):
    return shutil.which(name) is not None


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        pass
    except Exception as e:
        print("读取配置 %s 失败：%s，使用默认配置。" % (path, e), file=sys.stderr)
    cfg["_config_path"] = os.path.abspath(path)
    runtime_dir = os.environ.get("GPT_KEEPALIVE_STATE_DIR", SCRIPT_DIR)
    try:
        os.makedirs(runtime_dir, exist_ok=True)
    except OSError:
        pass
    if not cfg.get("log_file"):
        cfg["log_file"] = os.path.join(runtime_dir, "gpt-keepalive.log")
    if not cfg.get("state_file"):
        cfg["state_file"] = os.path.join(runtime_dir, "gpt-keepalive.state.json")
    return cfg


# ---------------------------------------------------------------- 状态存取

def load_state(cfg):
    try:
        with open(cfg["state_file"], "r", encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_state(cfg, st):
    tmp_path = "%s.tmp.%d" % (cfg["state_file"], os.getpid())
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, cfg["state_file"])
    except Exception as e:
        log(cfg, "保存状态失败：%s" % e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def effective_timer_hours(cfg, state):
    """返回当前生效的计时小时数；命令行覆盖值保存在状态文件中。"""
    try:
        hours = float(state.get("timer_hours", cfg["timer_hours"]))
    except (TypeError, ValueError):
        hours = float(cfg["timer_hours"])
    if hours <= 0:
        raise ValueError("timer_hours 必须大于 0")
    return hours


def effective_message(cfg, state):
    """返回当前生效的续活消息。"""
    return str(state.get("continue_message", cfg["continue_message"]))


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d 小时 %02d 分 %02d 秒" % (h, m, s)
    if m:
        return "%d 分 %02d 秒" % (m, s)
    return "%d 秒" % s


def parse_duration(text):
    """解析时长文本为秒：支持 '4h30m'、'4.5h'、'270m'、纯数字(按分钟)。"""
    text = text.strip().lower()
    total = 0.0
    compact = re.sub(r"\s+", "", text)
    parts = re.findall(r"(\d+(?:\.\d+)?)([hm])", compact)
    if parts and "".join(value + unit for value, unit in parts) == compact:
        for value, unit in parts:
            total += float(value) * (3600 if unit == "h" else 60)
    elif re.fullmatch(r"\d+(?:\.\d+)?", compact):
        total = float(compact) * 60
    else:
        raise ValueError("无法解析时长：%s（示例：4h30m / 4.5h / 270m）" % text)
    if total <= 0:
        raise ValueError("时长必须大于 0")
    return total


def parse_until(text):
    """解析到期时刻，返回本地 datetime：支持 'HH:MM' 与 'YYYY-MM-DD HH:MM'。"""
    text = text.strip()
    now = datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            target = datetime.strptime(text, fmt)
            if "%Y" not in fmt:
                target = target.replace(year=now.year, month=now.month, day=now.day)
                if target <= now:
                    target += timedelta(days=1)
            return target
        except ValueError:
            continue
    raise ValueError("无法解析时刻：%s（示例：21:30 或 2026-10-09 21:30）" % text)


def compute_expiry(cfg, hours, until, remaining):
    """按 until > remaining > hours > 默认 timer_hours 的优先级计算到期时间戳。"""
    if until:
        return parse_until(until).timestamp()
    if remaining:
        return time.time() + parse_duration(remaining)
    h = hours if hours is not None else cfg["timer_hours"]
    return time.time() + h * 3600


# ---------------------------------------------------------------- 窗口检测

def detect_x11(cfg, keywords):
    windows = []
    rc, out = run_cmd(["wmctrl", "-l"])
    if rc == 0:
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4:
                wid, title = parts[0], parts[3]
                if any(k.lower() in title.lower() for k in keywords):
                    windows.append((wid, title))
    if not windows and tool_available("xdotool"):
        for kw in keywords:
            rc, out = run_cmd(["xdotool", "search", "--name", kw])
            if rc == 0 and out:
                wid = out.splitlines()[0]
                windows.append((wid, "%s (xdotool)" % kw))
    return windows


def _walk_sway(node, keywords, out):
    name = node.get("name") or ""
    app_id = node.get("app_id") or ""
    if any(k.lower() in name.lower() or k.lower() in app_id.lower() for k in keywords):
        out.append((node.get("id"), name or app_id))
    for c in node.get("nodes", []) or []:
        _walk_sway(c, keywords, out)
    for c in node.get("floating_nodes", []) or []:
        _walk_sway(c, keywords, out)


def detect_sway(cfg, keywords):
    rc, out = run_cmd(["swaymsg", "-t", "get_tree"])
    if rc != 0 or not out:
        return []
    try:
        tree = json.loads(out)
    except Exception:
        return []
    res = []
    _walk_sway(tree, keywords, res)
    return res


def detect_hyprland(cfg, keywords):
    rc, out = run_cmd(["hyprctl", "-j", "clients"])
    if rc != 0 or not out:
        return []
    try:
        clients = json.loads(out)
    except Exception:
        return []
    res = []
    for c in clients:
        title = c.get("title") or ""
        cls = c.get("class") or ""
        if any(k.lower() in title.lower() or k.lower() in cls.lower() for k in keywords):
            res.append((c.get("address"), title or cls))
    return res


def detect_custom(cfg):
    if not cfg["custom_window_check"]:
        return []
    # 配置文件由本机用户维护；用 sh -c 以支持管道和重定向。
    rc, out = run_cmd(["/bin/sh", "-c", cfg["custom_window_check"]])
    if rc != 0:
        return []
    return [(None, line) for line in out.splitlines() if line.strip()]


def detect_windows(cfg, backend):
    if backend == "x11":
        return detect_x11(cfg, cfg["window_keywords"])
    if backend == "sway":
        return detect_sway(cfg, cfg["window_keywords"])
    if backend == "hyprland":
        return detect_hyprland(cfg, cfg["window_keywords"])
    if backend == "custom":
        res = detect_custom(cfg)
        if res:
            return res
        # custom 没结果时退回常见后端兜底
        res = detect_sway(cfg, cfg["window_keywords"]) or detect_hyprland(cfg, cfg["window_keywords"])
        if res:
            return res
        return detect_x11(cfg, cfg["window_keywords"])
    return []


# ---------------------------------------------------------------- 后端解析

def resolve_backends(cfg):
    wb = cfg["window_detector"]
    ib = cfg["input_backend"]
    session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    if wb == "auto":
        if "wayland" in session:
            if tool_available("swaymsg"):
                wb = "sway"
            elif tool_available("hyprctl"):
                wb = "hyprland"
            elif cfg["custom_window_check"]:
                wb = "custom"
            elif os.environ.get("DISPLAY") and (tool_available("wmctrl") or tool_available("xdotool")):
                # Wayland 会话中的 XWayland 窗口仍可能由 X11 工具控制。
                wb = "x11"
            else:
                wb = "none"
        else:
            if tool_available("wmctrl") or tool_available("xdotool"):
                wb = "x11"
            elif cfg["custom_window_check"]:
                wb = "custom"
            else:
                wb = "none"
    is_wayland = wb in ("sway", "hyprland") or "wayland" in session
    if ib == "auto":
        if is_wayland:
            if tool_available("wtype"):
                ib = "wtype"
            elif tool_available("wl-copy") and tool_available("ydotool"):
                ib = "ydotool-paste"
            elif tool_available("ydotool"):
                ib = "ydotool-type"
            else:
                ib = "none"
        else:
            if tool_available("xclip"):
                ib = "x11-paste"
            elif tool_available("xdotool"):
                ib = "x11-type"
            else:
                ib = "none"
    return wb, ib


# ---------------------------------------------------------------- 聚焦与发送

def focus_windows(cfg, backend, windows):
    if not windows:
        return False
    wid, title = windows[0]
    if cfg["custom_focus_command"]:
        cmd = cfg["custom_focus_command"].replace("{WINDOW_ID}", str(wid or "")).replace(
            "{KEYWORD}", str(cfg["window_keywords"][0]))
        rc, _ = run_cmd(shlex.split(cmd))
        return rc == 0
    if backend == "x11" and wid:
        rc, _ = run_cmd(["xdotool", "windowactivate", "--sync", str(wid)])
        return rc == 0
    if backend == "sway" and wid is not None:
        rc, _ = run_cmd(["swaymsg", '[con_id="%s"] focus' % wid])
        return rc == 0
    if backend == "hyprland" and wid:
        rc, _ = run_cmd(["hyprctl", "dispatch", "focuswindow", "address:%s" % wid])
        return rc == 0
    return False


def _send_paste_x11(msg):
    rc, _ = run_cmd_stdin(["xclip", "-selection", "clipboard", "-i"], msg)
    if rc != 0:
        return rc, "xclip 写入剪贴板失败"
    rc, err = run_cmd(["xdotool", "key", "ctrl+v"])
    if rc != 0:
        return rc, "xdotool 粘贴失败: %s" % err
    return run_cmd(["xdotool", "key", "Return"])


def _send_type_x11(msg):
    rc, err = run_cmd(["xdotool", "type", "--delay", "50", msg])
    if rc != 0:
        return rc, "xdotool 输入失败(中文可能需要 xclip 方案): %s" % err
    return run_cmd(["xdotool", "key", "Return"])


def _send_wtype(msg):
    rc, err = run_cmd(["wtype", msg])
    if rc != 0:
        return rc, "wtype 输入失败: %s" % err
    return run_cmd(["wtype", "-k", "Return"])


def _send_ydotool_type(msg):
    rc, err = run_cmd(["ydotool", "type", "--key-delay", "50", msg])
    if rc != 0:
        return rc, "ydotool type 失败: %s" % err
    return run_cmd(["ydotool", "key", "28:1", "28:0"])  # 28 = Enter


def _send_ydotool_paste(msg):
    rc, _ = run_cmd_stdin(["wl-copy", "--type", "text/plain;charset=utf-8"], msg)
    if rc != 0:
        return rc, "wl-copy 写入剪贴板失败"
    rc, err = run_cmd(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"])  # Ctrl+V
    if rc != 0:
        return rc, "ydotool 粘贴失败: %s" % err
    return run_cmd(["ydotool", "key", "28:1", "28:0"])  # Enter


def send_message(cfg, backend, msg, windows):
    if cfg["custom_input_command"]:
        wid = str(windows[0][0]) if windows and windows[0][0] else ""
        cmd = cfg["custom_input_command"].replace("{MSG}", msg).replace("{WINDOW_ID}", wid)
        return run_cmd(shlex.split(cmd))
    if backend == "x11-paste":
        return _send_paste_x11(msg)
    if backend == "x11-type":
        return _send_type_x11(msg)
    if backend == "wtype":
        return _send_wtype(msg)
    if backend == "ydotool-type":
        return _send_ydotool_type(msg)
    if backend == "ydotool-paste":
        return _send_ydotool_paste(msg)
    return -1, "没有可用的输入后端(backend=%s)，请检查依赖或配置 input_backend" % backend


# ---------------------------------------------------------------- OCR 自动识别

def check_ocr_auto_reset(cfg, state, now):
    """识别限额提示；只在“未命中 -> 命中”的边沿重置一次计时。"""
    if not cfg["ocr_command"]:
        return
    # OCR 通常需要截图、管道和命令替换，因此显式交给 shell 执行。
    # 该配置只能填写本机信任的命令。
    rc, out = run_cmd(["/bin/sh", "-c", cfg["ocr_command"]])
    if rc != 0:
        return
    matched = bool(re.search(cfg["limit_pattern"], out or "", re.IGNORECASE))
    was_active = bool(state.get("ocr_limit_active"))
    if matched == was_active:
        return
    state["ocr_limit_active"] = matched
    if matched:
        hours = effective_timer_hours(cfg, state)
        state["expires_at"] = now + hours * 3600
        state["auto_resets"] = state.get("auto_resets", 0) + 1
        state["missed_since"] = None
        state.pop("retry_after", None)
        log(cfg, "OCR 新检测到限额提示，已自动重置 %.2f 小时计时。" % hours)
    save_state(cfg, state)


# ---------------------------------------------------------------- 核心动作

def do_expiry_action(cfg, state, dry_run):
    now = time.time()
    wb, ib = resolve_backends(cfg)
    windows = detect_windows(cfg, wb)
    if not windows:
        state["missed_since"] = state.get("missed_since") or now
        waited = now - state["missed_since"]
        max_wait = cfg["max_wait_minutes_after_expiry"] * 60
        if waited < max_wait:
            # 每 5 分钟记一次日志，避免刷屏
            if now - state.get("miss_logged_at", 0) >= 300:
                state["miss_logged_at"] = now
                log(cfg, "到点但未检测到 ChatGPT 窗口，继续等待（%.0f 秒后放弃本轮）"
                    % (max_wait - waited))
            save_state(cfg, state)
            return
        log(cfg, "等待超时仍未检测到窗口，本轮跳过。")
        if cfg["launch_command"]:
            rc, _ = run_cmd(["/bin/sh", "-c", cfg["launch_command"]])
            log(cfg, "已尝试执行 launch_command（rc=%d）" % rc)
            if rc == 0:
                retry_seconds = max(1, int(cfg["action_retry_seconds"]))
                state["missed_since"] = None
                state["retry_after"] = now + retry_seconds
                save_state(cfg, state)
                log(cfg, "等待应用启动，%d 秒后重新检测窗口。" % retry_seconds)
                return
        state["missed_since"] = None
    else:
        state["missed_since"] = None
        if not focus_windows(cfg, wb, windows):
            retry_seconds = max(1, int(cfg["action_retry_seconds"]))
            state["retry_after"] = now + retry_seconds
            if now - state.get("action_failure_logged_at", 0) >= 300:
                state["action_failure_logged_at"] = now
                log(cfg, "检测到窗口但聚焦失败；不会发送消息，%d 秒后重试。" % retry_seconds)
            if cfg["notify"] and not state.get("action_failure_notified"):
                run_cmd([cfg["notify_command"], "GPT KeepAlive", "聚焦 ChatGPT 窗口失败，将自动重试"])
                state["action_failure_notified"] = True
            save_state(cfg, state)
            return
        time.sleep(0.5)
        message = effective_message(cfg, state)
        if dry_run:
            log(cfg, "[演练] 将向 ChatGPT 发送：%s" % message)
        else:
            rc, err = send_message(cfg, ib, message, windows)
            if rc == 0:
                state["cycle"] = state.get("cycle", 0) + 1
                log(cfg, "已向 ChatGPT 发送继续消息（第 %d 轮）。" % state["cycle"])
                if cfg["notify"]:
                    run_cmd([cfg["notify_command"], "GPT KeepAlive",
                             "已发送继续消息（第 %d 轮）" % state["cycle"]])
            else:
                retry_seconds = max(1, int(cfg["action_retry_seconds"]))
                state["retry_after"] = now + retry_seconds
                if now - state.get("action_failure_logged_at", 0) >= 300:
                    state["action_failure_logged_at"] = now
                    log(cfg, "发送消息失败，%d 秒后重试：%s" % (retry_seconds, err))
                if cfg["notify"] and not state.get("action_failure_notified"):
                    run_cmd([cfg["notify_command"], "GPT KeepAlive", "发送消息失败：" + err])
                    state["action_failure_notified"] = True
                save_state(cfg, state)
                return
    hours = effective_timer_hours(cfg, state)
    state["expires_at"] = now + hours * 3600
    state["last_action"] = now
    state.pop("retry_after", None)
    state.pop("action_failure_logged_at", None)
    state.pop("action_failure_notified", None)
    save_state(cfg, state)
    log(cfg, "下一轮计时：%.2f 小时后。" % hours)


# ---------------------------------------------------------------- 子命令

def cmd_check(cfg):
    wb, ib = resolve_backends(cfg)
    print("窗口检测后端 : %s" % wb)
    print("输入后端     : %s" % ib)
    windows = detect_windows(cfg, wb)
    if windows:
        print("ChatGPT 窗口 : 已检测到（%d 个）" % len(windows))
        for wid, title in windows[:5]:
            print("   - %s (id=%s)" % (title, wid))
        return 0
    print("ChatGPT 窗口 : 未检测到")
    if wb == "none":
        print("提示：未识别出可用的窗口检测工具。X11 装 wmctrl/xdotool；Wayland 用 swaymsg/hyprctl；"
              "或配置 custom_window_check。")
    return 1


def cmd_reset(cfg, hours, message, until, remaining):
    state = load_state(cfg)
    if hours is not None:
        state["timer_hours"] = hours
    if message is not None:
        state["continue_message"] = message
    active_hours = effective_timer_hours(cfg, state)
    state["expires_at"] = compute_expiry(cfg, active_hours, until, remaining)
    state["missed_since"] = None
    state["ocr_limit_active"] = True if cfg["ocr_command"] else False
    state.pop("retry_after", None)
    state.pop("action_failure_logged_at", None)
    state.pop("action_failure_notified", None)
    state["last_action"] = time.time()
    save_state(cfg, state)
    log(cfg, "计时已重置：将于 %s 到期（剩余 %s）。"
        % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["expires_at"])),
           fmt_duration(max(0, state["expires_at"] - time.time()))))
    print("继续消息：%s" % effective_message(cfg, state))


def cmd_status(cfg):
    state = load_state(cfg)
    wb, ib = resolve_backends(cfg)
    now = time.time()
    print("== GPT KeepAlive 状态 ==")
    print("窗口检测后端 : %s" % wb)
    print("输入后端     : %s" % ib)
    windows = detect_windows(cfg, wb)
    if windows:
        print("ChatGPT 窗口 : 已检测到（%d 个）" % len(windows))
        for wid, title in windows[:5]:
            print("   - %s (id=%s)" % (title, wid))
    else:
        print("ChatGPT 窗口 : 未检测到")
    exp = state.get("expires_at")
    if not exp:
        print("计时状态     : 尚未开始（运行 reset 或 run 开始计时）")
    else:
        print("计时剩余     : %s" % fmt_duration(max(0, exp - now)))
        print("到期时间     : %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp)))
        print("计时间隔     : %.2f 小时" % effective_timer_hours(cfg, state))
        print("继续消息     : %s" % effective_message(cfg, state))
        print("已执行轮次   : %d" % state.get("cycle", 0))
        print("OCR 自动重置 : %d 次" % state.get("auto_resets", 0))
    print("配置文件     : %s" % cfg["_config_path"])


def cmd_run(cfg, dry_run, hours, message, until, remaining):
    state = load_state(cfg)
    if hours is not None:
        state["timer_hours"] = hours
    if message is not None:
        state["continue_message"] = message
    if not state.get("expires_at"):
        state["expires_at"] = compute_expiry(
            cfg, effective_timer_hours(cfg, state), until, remaining)
        state["cycle"] = 0
        log(cfg, "首次运行，计时将于 %s 到期（剩余 %s）。"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["expires_at"])),
               fmt_duration(max(0, state["expires_at"] - time.time()))))

    save_state(cfg, state)

    stop = [False]

    def handler(signum, frame):
        stop[0] = True

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    wb, ib = resolve_backends(cfg)
    log(cfg, "守护进程启动（窗口检测=%s，输入=%s，演练=%s）。Ctrl+C 退出。"
        % (wb, ib, "是" if dry_run else "否"))

    last_ocr = 0
    while not stop[0]:
        # reset/status 可由另一个进程调用；每轮重读原子状态文件以接收更新。
        state = load_state(cfg)
        now = time.time()
        if cfg["ocr_command"] and now - last_ocr >= cfg["ocr_interval_seconds"]:
            last_ocr = now
            check_ocr_auto_reset(cfg, state, now)
        if (now >= state.get("expires_at", 0)
                and now >= state.get("retry_after", 0)):
            do_expiry_action(cfg, state, dry_run)
        time.sleep(cfg["check_interval_seconds"])
    log(cfg, "守护进程退出。")


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="GPT KeepAlive：检测 ChatGPT 运行并管理可配置倒计时")
    ap.add_argument("--config", default=os.path.join(SCRIPT_DIR, "config.json"),
                    help="配置文件路径（默认脚本同目录 config.json）")
    sub = ap.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="检测 ChatGPT 窗口是否在运行")
    p_reset = sub.add_parser("reset", help="从现在开始新的计时")
    p_reset.add_argument("--hours", type=float, help="覆盖计时小时数（默认 5）")
    p_reset.add_argument("--message", help="覆盖继续消息")
    p_reset.add_argument("--until", help="到期时刻，对齐界面显示的重置时间，如 21:30 或 '2026-10-09 21:30'")
    p_reset.add_argument("--remaining", help="剩余时长，如 4h30m / 4.5h / 270m")
    p_run = sub.add_parser("run", help="守护进程：计时并在到点后自动发送继续消息")
    p_run.add_argument("--dry-run", action="store_true", help="演练模式：只记录不发送")
    p_run.add_argument("--hours", type=float, help="覆盖计时小时数（默认 5）")
    p_run.add_argument("--message", help="覆盖继续消息")
    p_run.add_argument("--until", help="首次计时的到期时刻，如 21:30 或 '2026-10-09 21:30'")
    p_run.add_argument("--remaining", help="首次计时的剩余时长，如 4h30m / 4.5h / 270m")
    sub.add_parser("status", help="查看状态")

    args = ap.parse_args()
    cfg = load_config(args.config)

    try:
        if args.command == "check":
            sys.exit(cmd_check(cfg))
        elif args.command == "reset":
            cmd_reset(cfg, getattr(args, "hours", None), getattr(args, "message", None),
                      getattr(args, "until", None), getattr(args, "remaining", None))
        elif args.command == "run":
            cmd_run(cfg, args.dry_run, getattr(args, "hours", None), getattr(args, "message", None),
                    getattr(args, "until", None), getattr(args, "remaining", None))
        elif args.command == "status":
            cmd_status(cfg)
    except ValueError as e:
        print("参数错误：%s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
