#!/usr/bin/env python3
"""Tkinter desktop frontend for GPT Automatic Retry."""

import json
import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk


APP_TITLE = "GPT Automatic Retry"
SERVICE = "gpt-automatic-retry.service"
CLI = "/usr/bin/gpt-automatic-retry"


def xdg_path(env_name, fallback, *parts):
    root = os.environ.get(env_name) or os.path.expanduser(fallback)
    return Path(root).joinpath(*parts)


CONFIG_FILE = xdg_path("XDG_CONFIG_HOME", "~/.config", "gpt-automatic-retry", "config.json")
STATE_DIR = xdg_path("XDG_STATE_HOME", "~/.local/state", "gpt-automatic-retry")
LOG_FILE = STATE_DIR / "gpt-keepalive.log"


def run_process(args, timeout=25):
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
        return proc.returncode, output
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, "操作超时，请检查桌面会话和窗口控制依赖。"
    except Exception as exc:
        return 1, str(exc)


class KeepAliveApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x610")
        self.minsize(680, 520)
        self.busy = False

        self.until_var = tk.StringVar(value="21:30")
        self.duration_var = tk.StringVar(value="5h")
        self.message_var = tk.StringVar(value="请继续完成之前的任务")
        self.auto_start_var = tk.BooleanVar(value=True)
        self.service_var = tk.StringVar(value="服务状态：检查中…")

        self._build_ui()
        self.after(300, self.refresh_status)

    def _build_ui(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="GPT 自动重试", font=("Sans", 20, "bold")).pack(anchor="w")
        session = os.environ.get("XDG_SESSION_TYPE", "未知")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "未知")
        subtitle = "桌面会话：%s · %s" % (session, desktop)
        if session.lower() == "wayland":
            subtitle += "（Wayland 的窗口控制能力取决于桌面环境）"
        ttk.Label(root, text=subtitle).pack(anchor="w", pady=(2, 14))

        settings = ttk.LabelFrame(root, text="续接设置", padding=12)
        settings.pack(fill="x")
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="发送消息").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(settings, textvariable=self.message_var).grid(row=0, column=1, columnspan=3, sticky="ew", pady=5)

        ttk.Label(settings, text="重置时刻").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(settings, textvariable=self.until_var, width=14).grid(row=1, column=1, sticky="w", pady=5)
        ttk.Button(settings, text="按时刻重置", command=self.reset_until).grid(row=1, column=2, padx=6, pady=5)

        ttk.Label(settings, text="剩余时长").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(settings, textvariable=self.duration_var, width=14).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Button(settings, text="按时长重置", command=self.reset_duration).grid(row=2, column=2, padx=6, pady=5)
        ttk.Label(settings, text="示例：4h30m / 270m").grid(row=2, column=3, sticky="w", pady=5)

        ttk.Checkbutton(
            settings,
            text="重置后自动启动后台服务",
            variable=self.auto_start_var,
        ).grid(row=3, column=1, columnspan=3, sticky="w", pady=(8, 2))

        actions = ttk.LabelFrame(root, text="窗口与服务", padding=12)
        actions.pack(fill="x", pady=12)
        for idx in range(5):
            actions.columnconfigure(idx, weight=1)
        ttk.Button(actions, text="检测窗口", command=self.check_window).grid(row=0, column=0, padx=4, sticky="ew")
        ttk.Button(actions, text="启动服务", command=self.start_service).grid(row=0, column=1, padx=4, sticky="ew")
        ttk.Button(actions, text="停止服务", command=self.stop_service).grid(row=0, column=2, padx=4, sticky="ew")
        ttk.Button(actions, text="刷新状态", command=self.refresh_status).grid(row=0, column=3, padx=4, sticky="ew")
        ttk.Button(actions, text="打开配置", command=self.open_config).grid(row=0, column=4, padx=4, sticky="ew")
        ttk.Label(actions, textvariable=self.service_var).grid(row=1, column=0, columnspan=5, sticky="w", pady=(10, 0))

        output_frame = ttk.LabelFrame(root, text="状态与日志", padding=8)
        output_frame.pack(fill="both", expand=True)
        self.output = scrolledtext.ScrolledText(output_frame, wrap="word", height=14, font=("Monospace", 10))
        self.output.pack(fill="both", expand=True)
        self.output.configure(state="disabled")

        footer = ttk.Frame(root)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="打开日志", command=self.open_log).pack(side="left")
        ttk.Button(footer, text="退出", command=self.destroy).pack(side="right")

    def write_output(self, text):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", text or "（没有输出）")
        self.output.configure(state="disabled")

    def run_async(self, label, task, on_done=None, show_error=True):
        if self.busy:
            return
        self.busy = True
        self.service_var.set(label + "…")

        def worker():
            rc, output = task()

            def finish():
                self.busy = False
                self.write_output(output)
                if rc != 0 and show_error:
                    messagebox.showerror(APP_TITLE, output or (label + "失败"))
                if on_done:
                    on_done(rc, output)
                else:
                    self.update_service_label()

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def cli_task(self, *args):
        return run_process([CLI, *args])

    def check_window(self):
        self.run_async("正在检测窗口", lambda: self.cli_task("check"), show_error=False)

    def reset_until(self):
        until = self.until_var.get().strip()
        message = self.message_var.get().strip()
        if not until or not message:
            messagebox.showwarning(APP_TITLE, "请填写重置时刻和续接消息。")
            return
        self._reset(["reset", "--until", until, "--message", message])

    def reset_duration(self):
        duration = self.duration_var.get().strip()
        message = self.message_var.get().strip()
        if not duration or not message:
            messagebox.showwarning(APP_TITLE, "请填写剩余时长和续接消息。")
            return
        self._reset(["reset", "--remaining", duration, "--message", message])

    def _reset(self, args):
        def task():
            rc, output = self.cli_task(*args)
            if rc == 0 and self.auto_start_var.get():
                run_process(["systemctl", "--user", "daemon-reload"])
                service_rc, service_output = run_process(
                    ["systemctl", "--user", "enable", "--now", SERVICE]
                )
                if service_output:
                    output = output + "\n\n" + service_output
                rc = service_rc
            return rc, output

        self.run_async("正在重置计时", task)

    def start_service(self):
        def task():
            run_process(["systemctl", "--user", "daemon-reload"])
            return run_process(["systemctl", "--user", "enable", "--now", SERVICE])

        self.run_async("正在启动服务", task)

    def stop_service(self):
        self.run_async(
            "正在停止服务",
            lambda: run_process(["systemctl", "--user", "disable", "--now", SERVICE]),
        )

    def update_service_label(self):
        rc, output = run_process(["systemctl", "--user", "is-active", SERVICE], timeout=5)
        state = output.strip() if output else ("active" if rc == 0 else "inactive")
        labels = {"active": "运行中", "inactive": "已停止", "failed": "启动失败", "activating": "启动中"}
        self.service_var.set("服务状态：" + labels.get(state, state))

    def refresh_status(self):
        def task():
            rc, output = self.cli_task("status")
            service_rc, service_output = run_process(
                ["systemctl", "--user", "is-active", SERVICE], timeout=5
            )
            state = service_output.strip() or ("active" if service_rc == 0 else "inactive")
            return rc, output + "\n后台服务     : " + state

        self.run_async("正在刷新状态", task, show_error=False)

    def open_config(self):
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            if not CONFIG_FILE.exists():
                CONFIG_FILE.write_text(json.dumps({}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            rc, output = run_process(["xdg-open", str(CONFIG_FILE)], timeout=5)
            if rc != 0:
                raise RuntimeError(output)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, "无法打开配置文件：%s" % exc)

    def open_log(self):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            if not LOG_FILE.exists():
                LOG_FILE.touch()
            rc, output = run_process(["xdg-open", str(LOG_FILE)], timeout=5)
            if rc != 0:
                raise RuntimeError(output)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, "无法打开日志文件：%s" % exc)


if __name__ == "__main__":
    KeepAliveApp().mainloop()
