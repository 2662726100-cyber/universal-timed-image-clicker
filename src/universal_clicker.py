from __future__ import annotations

import copy
import ctypes
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk


APP_NAME = "通用定时图像点击器"
APP_VERSION = "2.0.0"
CONFIG_VERSION = 2
DEFAULT_TARGET_TIME = "09:30:00"
DEFAULT_THRESHOLD = 0.84
DEFAULT_MARGIN = 160
MAX_STEPS = 5
MAX_LATE_SECONDS = 30


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


enable_dpi_awareness()


def local_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = local_data_dir()
TEMPLATES_DIR = DATA_DIR / "按钮模板"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "配置.json"
LOG_PATH = DATA_DIR / "运行记录.txt"


def make_id() -> str:
    return uuid.uuid4().hex[:12]


def new_step(name: str) -> dict:
    return {
        "id": make_id(),
        "name": name,
        "timeout": 5.0,
        "delay_ms": 100,
        "template": None,
    }


def new_profile(name: str) -> dict:
    return {
        "id": make_id(),
        "name": name,
        "target_time": DEFAULT_TARGET_TIME,
        "threshold": DEFAULT_THRESHOLD,
        "margin": DEFAULT_MARGIN,
        "nearby_only": True,
        "formal_mode": False,
        "steps": [new_step("选择目标按钮"), new_step("确认或下单按钮")],
    }


def make_share_payload(profile: dict) -> dict:
    """Create a privacy-safe, device-independent profile export."""
    exported = copy.deepcopy(profile)
    exported["id"] = make_id()
    exported["formal_mode"] = False
    for step in exported.get("steps", []):
        step["id"] = make_id()
        step["template"] = None
    return {
        "format": "通用定时图像点击器方案",
        "version": CONFIG_VERSION,
        "note": "出于隐私和显示兼容考虑，本文件不包含按钮截图与屏幕坐标；导入后请重新框选。",
        "profile": exported,
    }


@dataclass
class TemplateInfo:
    path: Path
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_dict(cls, data: dict | None) -> "TemplateInfo | None":
        if not isinstance(data, dict):
            return None
        try:
            path = Path(str(data["path"]))
            if not path.exists():
                return None
            return cls(
                path=path,
                x=int(data["x"]),
                y=int(data["y"]),
                width=int(data["width"]),
                height=int(data["height"]),
            )
        except Exception:
            return None

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


class StopRequested(Exception):
    pass


class UniversalClickerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("940x820")
        self.root.minsize(900, 740)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.profiles: list[dict] = []
        self.current_profile_id = ""
        self.step_rows: list[dict] = []

        self.profile_name_var = tk.StringVar()
        self.target_time_var = tk.StringVar(value=DEFAULT_TARGET_TIME)
        self.threshold_var = tk.DoubleVar(value=DEFAULT_THRESHOLD)
        self.margin_var = tk.IntVar(value=DEFAULT_MARGIN)
        self.nearby_only_var = tk.BooleanVar(value=True)
        self.formal_mode_var = tk.BooleanVar(value=False)
        self.clock_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请选择或创建方案")

        self._load_config()
        self._build_ui()
        self._refresh_profile_selector()
        self._load_current_profile_to_ui()
        self.root.after(20, self._update_clock)
        self.root.after(30, self._drain_events)

    # ---------- configuration ----------

    def _normalize_step(self, raw: dict, index: int) -> dict:
        step = new_step(f"步骤 {index + 1}")
        if isinstance(raw, dict):
            step["id"] = str(raw.get("id") or step["id"])
            step["name"] = str(raw.get("name") or step["name"])[:60]
            try:
                step["timeout"] = min(60.0, max(0.2, float(raw.get("timeout", 5.0))))
            except Exception:
                pass
            try:
                step["delay_ms"] = min(10000, max(0, int(raw.get("delay_ms", 100))))
            except Exception:
                pass
            template = TemplateInfo.from_dict(raw.get("template"))
            step["template"] = template.to_dict() if template else None
        return step

    def _normalize_profile(self, raw: dict, fallback_name: str) -> dict:
        profile = new_profile(fallback_name)
        if not isinstance(raw, dict):
            return profile
        profile["id"] = str(raw.get("id") or profile["id"])
        profile["name"] = str(raw.get("name") or fallback_name)[:50]
        profile["target_time"] = str(raw.get("target_time") or DEFAULT_TARGET_TIME)
        try:
            profile["threshold"] = min(0.999, max(0.5, float(raw.get("threshold", DEFAULT_THRESHOLD))))
        except Exception:
            pass
        try:
            profile["margin"] = min(2000, max(20, int(raw.get("margin", DEFAULT_MARGIN))))
        except Exception:
            pass
        profile["nearby_only"] = bool(raw.get("nearby_only", True))
        profile["formal_mode"] = bool(raw.get("formal_mode", False))
        raw_steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
        if raw_steps:
            profile["steps"] = [
                self._normalize_step(step, i) for i, step in enumerate(raw_steps[:MAX_STEPS])
            ]
        return profile

    def _load_config(self) -> None:
        data = None
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = None
        if isinstance(data, dict) and isinstance(data.get("profiles"), list):
            self.profiles = [
                self._normalize_profile(item, f"方案 {i + 1}")
                for i, item in enumerate(data["profiles"])
                if isinstance(item, dict)
            ]
            requested = str(data.get("selected_profile_id") or "")
            if any(profile["id"] == requested for profile in self.profiles):
                self.current_profile_id = requested
        if not self.profiles:
            self.profiles = [new_profile("默认方案")]
        if not self.current_profile_id:
            self.current_profile_id = self.profiles[0]["id"]

    def _save_config(self) -> None:
        data = {
            "version": CONFIG_VERSION,
            "selected_profile_id": self.current_profile_id,
            "profiles": self.profiles,
        }
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _current_profile(self) -> dict:
        for profile in self.profiles:
            if profile["id"] == self.current_profile_id:
                return profile
        self.current_profile_id = self.profiles[0]["id"]
        return self.profiles[0]

    def _sync_ui_to_profile(self, show_errors: bool = False) -> bool:
        profile = self._current_profile()
        try:
            profile["target_time"] = self.target_time_var.get().strip()
            profile["threshold"] = float(self.threshold_var.get())
            profile["margin"] = int(self.margin_var.get())
            profile["nearby_only"] = bool(self.nearby_only_var.get())
            profile["formal_mode"] = bool(self.formal_mode_var.get())
            for row, step in zip(self.step_rows, profile["steps"]):
                step["name"] = row["name"].get().strip() or "未命名步骤"
                step["timeout"] = float(row["timeout"].get())
                step["delay_ms"] = int(row["delay"].get())
            self._save_config()
            return True
        except (ValueError, tk.TclError) as exc:
            if show_errors:
                messagebox.showerror("设置有误", f"请检查相似度、搜索范围、超时和等待时间。\n{exc}")
            return False

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(5, weight=1)

        ttk.Label(
            self.root,
            text="通用定时图像点击器",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(16, 2))
        ttk.Label(
            self.root,
            textvariable=self.clock_var,
            font=("Consolas", 20, "bold"),
            foreground="#146c43",
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 10))

        profiles = ttk.LabelFrame(self.root, text="1. 方案管理")
        profiles.grid(row=2, column=0, sticky="ew", padx=22)
        profiles.columnconfigure(0, weight=1)
        self.profile_combo = ttk.Combobox(
            profiles, textvariable=self.profile_name_var, state="readonly", width=36
        )
        self.profile_combo.grid(row=0, column=0, padx=(10, 6), pady=10, sticky="ew")
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        ttk.Button(profiles, text="新建", command=self.new_profile_ui).grid(row=0, column=1, padx=3)
        ttk.Button(profiles, text="重命名", command=self.rename_profile_ui).grid(row=0, column=2, padx=3)
        ttk.Button(profiles, text="删除", command=self.delete_profile_ui).grid(row=0, column=3, padx=3)
        ttk.Button(profiles, text="导出方案", command=self.export_profile).grid(row=0, column=4, padx=3)
        ttk.Button(profiles, text="导入方案", command=self.import_profile).grid(
            row=0, column=5, padx=(3, 10)
        )

        settings = ttk.LabelFrame(self.root, text="2. 时间与识别设置")
        settings.grid(row=3, column=0, sticky="ew", padx=22, pady=(10, 0))
        ttk.Label(settings, text="每日触发时间：").grid(row=0, column=0, padx=(10, 2), pady=(10, 6))
        ttk.Entry(settings, width=11, textvariable=self.target_time_var, font=("Consolas", 11)).grid(
            row=0, column=1, padx=(0, 14), pady=(10, 6)
        )
        ttk.Label(settings, text="相似度：").grid(row=0, column=2, padx=(0, 2), pady=(10, 6))
        ttk.Spinbox(
            settings, from_=0.50, to=0.999, increment=0.01, width=7, textvariable=self.threshold_var
        ).grid(row=0, column=3, padx=(0, 14), pady=(10, 6))
        ttk.Label(settings, text="搜索范围(px)：").grid(row=0, column=4, padx=(0, 2), pady=(10, 6))
        ttk.Spinbox(
            settings, from_=20, to=2000, increment=10, width=8, textvariable=self.margin_var
        ).grid(row=0, column=5, padx=(0, 12), pady=(10, 6))
        ttk.Checkbutton(
            settings,
            text="仅在框选位置附近搜索（更快）",
            variable=self.nearby_only_var,
        ).grid(row=1, column=0, columnspan=3, padx=10, pady=(2, 10), sticky="w")
        ttk.Checkbutton(
            settings,
            text="正式执行点击（关闭时只识别第一步并停止）",
            variable=self.formal_mode_var,
        ).grid(row=1, column=3, columnspan=3, padx=6, pady=(2, 10), sticky="w")

        self.steps_box = ttk.LabelFrame(self.root, text="3. 自定义操作步骤（最多 5 步）")
        self.steps_box.grid(row=4, column=0, sticky="ew", padx=22, pady=(10, 0))
        self.steps_box.columnconfigure(0, weight=1)
        self.steps_container = ttk.Frame(self.steps_box)
        self.steps_container.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 3))
        self.steps_container.columnconfigure(1, weight=1)
        self.add_step_button = ttk.Button(self.steps_box, text="＋ 添加步骤", command=self.add_step)
        self.add_step_button.grid(row=1, column=0, sticky="w", padx=9, pady=(3, 9))

        controls = ttk.Frame(self.root)
        controls.grid(row=5, column=0, sticky="nsew", padx=22, pady=10)
        controls.columnconfigure((0, 1), weight=1)
        controls.rowconfigure(2, weight=1)
        self.arm_button = ttk.Button(controls, text="启用当前方案", command=self.arm)
        self.arm_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.stop_button = ttk.Button(
            controls, text="立即停止（Esc）", command=self.stop, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(controls, textvariable=self.status_var, foreground="#0d6efd").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 5)
        )
        log_box = ttk.Frame(controls)
        log_box.grid(row=2, column=0, columnspan=2, sticky="nsew")
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_box, height=8, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9)
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(log_box, command=self.log_text.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=bar.set)

        ttk.Label(
            self.root,
            text="正式使用前请逐步测试。不同电脑、分辨率、显示缩放或页面主题都应重新框选按钮。",
            foreground="#6c757d",
        ).grid(row=6, column=0, sticky="w", padx=22, pady=(0, 14))

    def _refresh_profile_selector(self) -> None:
        names = [profile["name"] for profile in self.profiles]
        self.profile_combo["values"] = names
        index = next(
            (i for i, profile in enumerate(self.profiles) if profile["id"] == self.current_profile_id),
            0,
        )
        self.profile_combo.current(index)

    def _load_current_profile_to_ui(self) -> None:
        profile = self._current_profile()
        self.target_time_var.set(profile["target_time"])
        self.threshold_var.set(profile["threshold"])
        self.margin_var.set(profile["margin"])
        self.nearby_only_var.set(profile["nearby_only"])
        self.formal_mode_var.set(profile["formal_mode"])
        self._rebuild_steps()
        self.status_var.set(f"当前方案：{profile['name']}（尚未启用）")

    def _rebuild_steps(self) -> None:
        for child in self.steps_container.winfo_children():
            child.destroy()
        self.step_rows.clear()
        headers = ("序号", "步骤名称", "模板状态", "超时(秒)", "点击后等待(ms)", "操作")
        for col, header in enumerate(headers):
            ttk.Label(self.steps_container, text=header, font=("Microsoft YaHei UI", 9, "bold")).grid(
                row=0, column=col, padx=4, pady=(0, 5), sticky="w"
            )
        profile = self._current_profile()
        for index, step in enumerate(profile["steps"]):
            name_var = tk.StringVar(value=step["name"])
            timeout_var = tk.DoubleVar(value=step["timeout"])
            delay_var = tk.IntVar(value=step["delay_ms"])
            template = TemplateInfo.from_dict(step.get("template"))
            state_var = tk.StringVar(
                value=(f"已框选 {template.width}×{template.height}" if template else "未框选")
            )
            row = index + 1
            ttk.Label(self.steps_container, text=str(index + 1)).grid(row=row, column=0, padx=4, pady=3)
            ttk.Entry(self.steps_container, textvariable=name_var, width=25).grid(
                row=row, column=1, padx=4, pady=3, sticky="ew"
            )
            ttk.Label(self.steps_container, textvariable=state_var, width=17).grid(
                row=row, column=2, padx=4, pady=3, sticky="w"
            )
            ttk.Spinbox(
                self.steps_container,
                from_=0.2,
                to=60,
                increment=0.5,
                width=7,
                textvariable=timeout_var,
            ).grid(row=row, column=3, padx=4, pady=3)
            ttk.Spinbox(
                self.steps_container,
                from_=0,
                to=10000,
                increment=50,
                width=9,
                textvariable=delay_var,
            ).grid(row=row, column=4, padx=4, pady=3)
            actions = ttk.Frame(self.steps_container)
            actions.grid(row=row, column=5, padx=2, pady=3)
            ttk.Button(actions, text="框选", width=6, command=lambda i=index: self.capture_step(i)).pack(
                side="left", padx=2
            )
            ttk.Button(actions, text="测试", width=6, command=lambda i=index: self.test_step(i)).pack(
                side="left", padx=2
            )
            ttk.Button(actions, text="移除", width=6, command=lambda i=index: self.remove_step(i)).pack(
                side="left", padx=2
            )
            self.step_rows.append(
                {"name": name_var, "timeout": timeout_var, "delay": delay_var, "state": state_var}
            )
        self.add_step_button.configure(
            state="disabled" if len(profile["steps"]) >= MAX_STEPS else "normal"
        )

    def _update_clock(self) -> None:
        now = datetime.now()
        self.clock_var.set(now.strftime("系统时间  %Y-%m-%d  %H:%M:%S.") + f"{now.microsecond // 1000:03d}")
        self.root.after(20, self._update_clock)

    def _write_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{stamp}] {message}"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            with LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(datetime.now().strftime("%Y-%m-%d ") + line + "\n")
        except Exception:
            pass

    def _busy(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def _reject_if_busy(self) -> bool:
        if self._busy():
            messagebox.showwarning("任务正在运行", "请先停止当前任务，再修改方案或步骤。")
            return True
        return False

    # ---------- profile and step management ----------

    def _on_profile_selected(self, _event=None) -> None:
        selected = self.profile_combo.current()
        if selected < 0 or selected >= len(self.profiles):
            return
        if self._busy():
            self._refresh_profile_selector()
            messagebox.showwarning("任务正在运行", "运行期间不能切换方案。")
            return
        self._sync_ui_to_profile()
        self.current_profile_id = self.profiles[selected]["id"]
        self._load_current_profile_to_ui()
        self._save_config()

    def _unique_profile_name(self, requested: str) -> str:
        base = requested.strip()[:50] or "新方案"
        names = {profile["name"] for profile in self.profiles}
        if base not in names:
            return base
        number = 2
        while f"{base} {number}" in names:
            number += 1
        return f"{base} {number}"

    def new_profile_ui(self) -> None:
        if self._reject_if_busy():
            return
        name = simpledialog.askstring("新建方案", "请输入方案名称：", parent=self.root)
        if name is None:
            return
        self._sync_ui_to_profile()
        profile = new_profile(self._unique_profile_name(name))
        self.profiles.append(profile)
        self.current_profile_id = profile["id"]
        self._refresh_profile_selector()
        self._load_current_profile_to_ui()
        self._save_config()

    def rename_profile_ui(self) -> None:
        if self._reject_if_busy():
            return
        profile = self._current_profile()
        name = simpledialog.askstring(
            "重命名方案", "请输入新名称：", initialvalue=profile["name"], parent=self.root
        )
        if name is None or not name.strip():
            return
        other_names = {item["name"] for item in self.profiles if item["id"] != profile["id"]}
        proposed = name.strip()[:50]
        if proposed in other_names:
            messagebox.showerror("名称重复", "已有同名方案，请换一个名称。")
            return
        profile["name"] = proposed
        self._refresh_profile_selector()
        self._save_config()

    def delete_profile_ui(self) -> None:
        if self._reject_if_busy():
            return
        if len(self.profiles) <= 1:
            messagebox.showwarning("不能删除", "至少需要保留一个方案。")
            return
        profile = self._current_profile()
        if not messagebox.askyesno(
            "删除方案", f"确定删除“{profile['name']}”及其按钮模板吗？此操作不能撤销。"
        ):
            return
        for step in profile["steps"]:
            template = TemplateInfo.from_dict(step.get("template"))
            if template and template.path.parent == TEMPLATES_DIR:
                try:
                    template.path.unlink(missing_ok=True)
                except Exception:
                    pass
        self.profiles = [item for item in self.profiles if item["id"] != profile["id"]]
        self.current_profile_id = self.profiles[0]["id"]
        self._refresh_profile_selector()
        self._load_current_profile_to_ui()
        self._save_config()

    def add_step(self) -> None:
        if self._reject_if_busy() or not self._sync_ui_to_profile(show_errors=True):
            return
        profile = self._current_profile()
        if len(profile["steps"]) >= MAX_STEPS:
            return
        profile["steps"].append(new_step(f"步骤 {len(profile['steps']) + 1}"))
        self._rebuild_steps()
        self._save_config()

    def remove_step(self, index: int) -> None:
        if self._reject_if_busy() or not self._sync_ui_to_profile(show_errors=True):
            return
        profile = self._current_profile()
        if len(profile["steps"]) <= 1:
            messagebox.showwarning("不能移除", "每个方案至少需要一个步骤。")
            return
        if index >= len(profile["steps"]):
            return
        step = profile["steps"][index]
        if not messagebox.askyesno("移除步骤", f"确定移除“{step['name']}”吗？"):
            return
        template = TemplateInfo.from_dict(step.get("template"))
        if template and template.path.parent == TEMPLATES_DIR:
            try:
                template.path.unlink(missing_ok=True)
            except Exception:
                pass
        del profile["steps"][index]
        self._rebuild_steps()
        self._save_config()

    def export_profile(self) -> None:
        if self._reject_if_busy() or not self._sync_ui_to_profile(show_errors=True):
            return
        profile = copy.deepcopy(self._current_profile())
        payload = make_share_payload(profile)
        safe_name = "".join("_" if char in '<>:"/\\|?*' else char for char in profile["name"])
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出当前方案",
            defaultextension=".json",
            initialfile=f"{safe_name}.json",
            filetypes=[("方案文件", "*.json")],
        )
        if not path:
            return
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        messagebox.showinfo("导出完成", "方案设置已导出。按钮截图、坐标和运行记录未包含在文件中。")

    def import_profile(self) -> None:
        if self._reject_if_busy():
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="导入方案",
            filetypes=[("方案文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if payload.get("format") != "通用定时图像点击器方案":
                raise ValueError("不是受支持的公版方案文件")
            raw = payload.get("profile")
            if not isinstance(raw, dict):
                raise ValueError("方案内容缺失")
            self._sync_ui_to_profile()
            profile = self._normalize_profile(raw, "导入方案")
            profile["id"] = make_id()
            profile["name"] = self._unique_profile_name(profile["name"])
            profile["formal_mode"] = False
            for step in profile["steps"]:
                step["id"] = make_id()
                step["template"] = None
            self.profiles.append(profile)
            self.current_profile_id = profile["id"]
            self._refresh_profile_selector()
            self._load_current_profile_to_ui()
            self._save_config()
            messagebox.showinfo("导入完成", "方案设置已导入。请在这台电脑上重新框选并测试每一个步骤。")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))

    # ---------- capture and matching ----------

    def capture_step(self, index: int) -> None:
        if self._reject_if_busy() or not self._sync_ui_to_profile(show_errors=True):
            return
        profile = self._current_profile()
        if index >= len(profile["steps"]):
            return
        step = profile["steps"][index]
        self.root.withdraw()
        self.root.after(
            250,
            lambda: self._open_selection_overlay(profile["id"], step["id"], step["name"]),
        )

    def _open_selection_overlay(self, profile_id: str, step_id: str, step_name: str) -> None:
        try:
            with mss.mss() as camera:
                monitor = camera.monitors[0]
                raw = np.asarray(camera.grab(monitor))
            rgb = cv2.cvtColor(raw, cv2.COLOR_BGRA2RGB)
            image = Image.fromarray(rgb)
            overlay = tk.Toplevel(self.root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            try:
                overlay.attributes("-alpha", 0.95)
            except tk.TclError:
                pass
            overlay.geometry(
                f"{monitor['width']}x{monitor['height']}+{monitor['left']}+{monitor['top']}"
            )
            canvas = tk.Canvas(overlay, highlightthickness=0, cursor="crosshair")
            canvas.pack(fill="both", expand=True)
            photo = ImageTk.PhotoImage(image)
            canvas.create_image(0, 0, anchor="nw", image=photo)
            canvas.image = photo
            canvas.create_rectangle(8, 8, 760, 52, fill="#111111", outline="#111111")
            canvas.create_text(
                20,
                30,
                anchor="w",
                fill="white",
                font=("Microsoft YaHei UI", 14, "bold"),
                text=f"拖动鼠标完整框住“{step_name}”；按 Esc 取消",
            )
            state: dict[str, int | None] = {"x": None, "y": None, "rect": None}

            def cancel(_event=None) -> None:
                overlay.destroy()
                self.root.deiconify()
                self.root.lift()

            def press(event) -> None:
                state["x"], state["y"] = event.x, event.y
                if state["rect"] is not None:
                    canvas.delete(state["rect"])
                state["rect"] = canvas.create_rectangle(
                    event.x, event.y, event.x, event.y, outline="#ff2d55", width=3
                )

            def drag(event) -> None:
                if state["x"] is not None and state["rect"] is not None:
                    canvas.coords(state["rect"], state["x"], state["y"], event.x, event.y)

            def release(event) -> None:
                if state["x"] is None or state["y"] is None:
                    return
                x1, x2 = sorted((int(state["x"]), int(event.x)))
                y1, y2 = sorted((int(state["y"]), int(event.y)))
                if x2 - x1 < 12 or y2 - y1 < 12:
                    messagebox.showwarning("框选太小", "请完整框住目标按钮。", parent=overlay)
                    return
                profile = next((p for p in self.profiles if p["id"] == profile_id), None)
                step = None if profile is None else next(
                    (s for s in profile["steps"] if s["id"] == step_id), None
                )
                if step is None:
                    cancel()
                    return
                path = TEMPLATES_DIR / f"{profile_id}_{step_id}.png"
                cropped = cv2.cvtColor(rgb[y1:y2, x1:x2], cv2.COLOR_RGB2BGR)
                encoded_ok, encoded = cv2.imencode(".png", cropped)
                if not encoded_ok:
                    raise RuntimeError("按钮模板保存失败")
                encoded.tofile(str(path))
                if not path.exists() or path.stat().st_size == 0:
                    raise RuntimeError("按钮模板保存失败")
                info = TemplateInfo(
                    path=path,
                    x=x1 + int(monitor["left"]),
                    y=y1 + int(monitor["top"]),
                    width=x2 - x1,
                    height=y2 - y1,
                )
                step["template"] = info.to_dict()
                self._save_config()
                overlay.destroy()
                self.root.deiconify()
                self.root.lift()
                self._rebuild_steps()
                self._write_log(f"“{step['name']}”模板已更新。")

            overlay.bind("<Escape>", cancel)
            canvas.bind("<ButtonPress-1>", press)
            canvas.bind("<B1-Motion>", drag)
            canvas.bind("<ButtonRelease-1>", release)
            overlay.focus_force()
        except Exception as exc:
            self.root.deiconify()
            messagebox.showerror("无法框选", f"截屏或框选界面启动失败：\n{exc}")

    @staticmethod
    def _read_template(path: Path) -> np.ndarray:
        data = np.fromfile(str(path), dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise RuntimeError(f"无法读取按钮模板：{path}")
        return image

    @staticmethod
    def _search_region(camera: mss.mss, template: TemplateInfo, margin: int, nearby_only: bool) -> dict:
        virtual = camera.monitors[0]
        if not nearby_only:
            return dict(virtual)
        left = max(int(virtual["left"]), template.x - margin)
        top = max(int(virtual["top"]), template.y - margin)
        right = min(
            int(virtual["left"] + virtual["width"]), template.x + template.width + margin
        )
        bottom = min(
            int(virtual["top"] + virtual["height"]), template.y + template.height + margin
        )
        return {"left": left, "top": top, "width": right - left, "height": bottom - top}

    def _find_once(
        self,
        camera: mss.mss,
        template: TemplateInfo,
        needle: np.ndarray,
        threshold: float,
        margin: int,
        nearby_only: bool,
    ) -> tuple[int, int, float] | None:
        region = self._search_region(camera, template, margin, nearby_only)
        raw = np.asarray(camera.grab(region))
        haystack = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
        if haystack.shape[0] < needle.shape[0] or haystack.shape[1] < needle.shape[1]:
            return None
        result = cv2.matchTemplate(haystack, needle, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        if not np.isfinite(score) or score < threshold:
            return None
        return (
            int(region["left"] + location[0] + needle.shape[1] / 2),
            int(region["top"] + location[1] + needle.shape[0] / 2),
            float(score),
        )

    def _find_until(
        self,
        camera: mss.mss,
        step: dict,
        template: TemplateInfo,
        needle: np.ndarray,
        threshold: float,
        margin: int,
        nearby_only: bool,
    ) -> tuple[int, int, float]:
        deadline = time.perf_counter() + float(step["timeout"])
        announced = False
        while time.perf_counter() < deadline:
            self._check_stop()
            found = self._find_once(camera, template, needle, threshold, margin, nearby_only)
            if found is not None:
                return found
            if not announced:
                self.events.put(("log", f"“{step['name']}”尚未出现，正在重试……"))
                announced = True
            time.sleep(0.002)
        raise TimeoutError(
            f"“{step['name']}”在 {step['timeout']} 秒内没有识别到，任务已停止。"
        )

    # ---------- execution ----------

    def _validated_profile(self) -> tuple[dict, datetime] | None:
        if not self._sync_ui_to_profile(show_errors=True):
            return None
        profile = copy.deepcopy(self._current_profile())
        try:
            parsed = datetime.strptime(profile["target_time"], "%H:%M:%S")
            if not 0.5 <= float(profile["threshold"]) <= 0.999:
                raise ValueError("相似度必须在 0.50～0.999 之间")
            if not 20 <= int(profile["margin"]) <= 2000:
                raise ValueError("搜索范围必须在 20～2000 像素之间")
            if not 1 <= len(profile["steps"]) <= MAX_STEPS:
                raise ValueError("每个方案需要 1～5 个步骤")
            for step in profile["steps"]:
                if not step["name"].strip():
                    raise ValueError("步骤名称不能为空")
                if not 0.2 <= float(step["timeout"]) <= 60:
                    raise ValueError(f"“{step['name']}”超时必须在 0.2～60 秒之间")
                if not 0 <= int(step["delay_ms"]) <= 10000:
                    raise ValueError(f"“{step['name']}”等待时间必须在 0～10000 毫秒之间")
                if TemplateInfo.from_dict(step.get("template")) is None:
                    raise ValueError(f"“{step['name']}”尚未框选有效模板")
        except ValueError as exc:
            messagebox.showerror("方案不能启用", str(exc))
            return None
        now = datetime.now()
        target = now.replace(
            hour=parsed.hour, minute=parsed.minute, second=parsed.second, microsecond=0
        )
        if target <= now:
            target += timedelta(days=1)
        return profile, target

    def test_step(self, index: int) -> None:
        if self._reject_if_busy() or not self._sync_ui_to_profile(show_errors=True):
            return
        profile = copy.deepcopy(self._current_profile())
        if index >= len(profile["steps"]):
            return
        step = profile["steps"][index]
        template = TemplateInfo.from_dict(step.get("template"))
        if template is None:
            messagebox.showwarning("尚未框选", f"请先框选“{step['name']}”。")
            return
        self.stop_event.clear()
        self._set_running_ui(True)
        self.status_var.set(f"正在测试“{step['name']}”（不会点击）……")
        self.worker = threading.Thread(
            target=self._test_worker,
            args=(profile, step, template),
            daemon=True,
        )
        self.worker.start()
        self.root.iconify()

    def _test_worker(self, profile: dict, step: dict, template: TemplateInfo) -> None:
        try:
            needle = self._read_template(template.path)
            time.sleep(0.35)
            with mss.mss() as camera:
                started = time.perf_counter()
                found = self._find_until(
                    camera,
                    step,
                    template,
                    needle,
                    float(profile["threshold"]),
                    int(profile["margin"]),
                    bool(profile["nearby_only"]),
                )
            elapsed = (time.perf_counter() - started) * 1000
            self.events.put(
                (
                    "finished",
                    f"测试通过：“{step['name']}”位于 ({found[0]}, {found[1]})，相似度 {found[2]:.3f}，耗时 {elapsed:.3f} ms；没有点击。",
                )
            )
        except StopRequested:
            self.events.put(("stopped", "识别测试已停止。"))
        except Exception as exc:
            self.events.put(("failed", str(exc)))

    def arm(self) -> None:
        if self._reject_if_busy():
            return
        validated = self._validated_profile()
        if validated is None:
            return
        profile, target = validated
        if profile["formal_mode"]:
            names = " → ".join(step["name"] for step in profile["steps"])
            if not messagebox.askyesno(
                "确认正式执行",
                f"方案“{profile['name']}”将在 {target.strftime('%Y-%m-%d %H:%M:%S')} 自动点击：\n\n{names}\n\n这可能产生真实预约、订单或费用。确认启用吗？",
            ):
                return
        else:
            messagebox.showinfo(
                "演练模式",
                "当前未开启“正式执行点击”。到达目标时间后只识别第一个步骤并停止，不会移动鼠标或点击。",
            )
        self.stop_event.clear()
        self._set_running_ui(True)
        self.status_var.set(f"已启用：等待 {target.strftime('%Y-%m-%d %H:%M:%S')}")
        self._write_log(
            f"方案“{profile['name']}”已启用，目标 {target.strftime('%Y-%m-%d %H:%M:%S')}，模式={'正式点击' if profile['formal_mode'] else '演练'}。"
        )
        self.worker = threading.Thread(
            target=self._scheduled_worker,
            args=(profile, target),
            daemon=True,
        )
        self.worker.start()
        self.root.iconify()

    def _scheduled_worker(self, profile: dict, target: datetime) -> None:
        winmm = ctypes.windll.winmm
        timer_period_set = False
        try:
            winmm.timeBeginPeriod(1)
            timer_period_set = True
        except Exception:
            pass
        try:
            prepared: list[tuple[dict, TemplateInfo, np.ndarray]] = []
            for step in profile["steps"]:
                template = TemplateInfo.from_dict(step.get("template"))
                if template is None:
                    raise RuntimeError(f"“{step['name']}”模板在运行前失效")
                prepared.append((step, template, self._read_template(template.path)))
            with mss.mss() as camera:
                self._wait_for_target(target)
                actual = datetime.now()
                late = (actual - target).total_seconds()
                if late > MAX_LATE_SECONDS:
                    raise RuntimeError(
                        f"电脑可能经历了休眠或长时间卡顿，当前已晚于目标 {late:.1f} 秒。为避免误点，任务已取消。"
                    )
                self.events.put(("log", f"已跨过目标时间，触发偏差 {late * 1000:+.3f} ms。"))
                if not profile["formal_mode"]:
                    step, template, needle = prepared[0]
                    found = self._find_until(
                        camera,
                        step,
                        template,
                        needle,
                        float(profile["threshold"]),
                        int(profile["margin"]),
                        bool(profile["nearby_only"]),
                    )
                    self.events.put(
                        (
                            "finished",
                            f"演练完成：已识别“{step['name']}”，位置 ({found[0]}, {found[1]})，相似度 {found[2]:.3f}；未点击。",
                        )
                    )
                    return
                for number, (step, template, needle) in enumerate(prepared, start=1):
                    self.events.put(("status", f"正在执行第 {number}/{len(prepared)} 步：{step['name']}"))
                    found = self._find_until(
                        camera,
                        step,
                        template,
                        needle,
                        float(profile["threshold"]),
                        int(profile["margin"]),
                        bool(profile["nearby_only"]),
                    )
                    self._click(found[0], found[1])
                    elapsed_ms = (datetime.now() - target).total_seconds() * 1000
                    self.events.put(
                        (
                            "log",
                            f"第 {number} 步“{step['name']}”已点击：({found[0]}, {found[1]})，相似度 {found[2]:.3f}，距目标 {elapsed_ms:.3f} ms。",
                        )
                    )
                    delay = int(step["delay_ms"])
                    if delay > 0 and number < len(prepared):
                        self._interruptible_delay(delay / 1000)
                self.events.put(("finished", f"方案“{profile['name']}”的 {len(prepared)} 个步骤均已完成。请核对目标页面结果。"))
        except StopRequested:
            self.events.put(("stopped", "任务已停止，未再执行点击。"))
        except Exception as exc:
            self.events.put(("failed", str(exc)))
        finally:
            if timer_period_set:
                try:
                    winmm.timeEndPeriod(1)
                except Exception:
                    pass

    def _wait_for_target(self, target: datetime) -> None:
        last_second = None
        while True:
            self._check_stop()
            remaining = (target - datetime.now()).total_seconds()
            if remaining <= 0:
                return
            whole = int(remaining)
            if whole != last_second and (whole <= 10 or whole % 60 == 0):
                last_second = whole
                self.events.put(("status", f"距触发约 {remaining:.3f} 秒（Esc 可停止）"))
            if remaining > 1:
                time.sleep(min(0.2, remaining - 0.8))
            elif remaining > 0.010:
                time.sleep(max(0.001, remaining - 0.004))

    def _interruptible_delay(self, seconds: float) -> None:
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            self._check_stop()
            time.sleep(min(0.01, max(0, deadline - time.perf_counter())))

    def _check_stop(self) -> None:
        if self.stop_event.is_set() or (ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000):
            self.stop_event.set()
            raise StopRequested

    @staticmethod
    def _click(x: int, y: int) -> None:
        user32 = ctypes.windll.user32
        if not user32.SetCursorPos(int(x), int(y)):
            raise RuntimeError("无法移动鼠标到目标位置")
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)

    def stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("正在停止……")

    def _set_running_ui(self, running: bool) -> None:
        self.arm_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.add_step_button.configure(state="disabled" if running else "normal")

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                message = str(payload)
                if event == "log":
                    self._write_log(message)
                elif event == "status":
                    self.status_var.set(message)
                elif event in {"finished", "failed", "stopped"}:
                    self._write_log(message)
                    self.status_var.set(message)
                    self.stop_event.set()
                    self._set_running_ui(False)
                    self._rebuild_steps()
                    self.root.deiconify()
                    self.root.lift()
                    if event == "failed":
                        messagebox.showerror("任务未完成", message)
                    elif event == "finished":
                        messagebox.showinfo("完成", message)
        except queue.Empty:
            pass
        finally:
            self.root.after(30, self._drain_events)

    def on_close(self) -> None:
        if self._busy():
            if not messagebox.askyesno("退出程序", "任务仍在运行。确定停止任务并退出吗？"):
                return
            self.stop_event.set()
        self._sync_ui_to_profile()
        self.root.destroy()


_MUTEX_HANDLE = None


def main() -> None:
    global _MUTEX_HANDLE
    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Local\\UniversalTimedImageClickerV2")
    already_running = kernel32.GetLastError() == 183
    root = tk.Tk()
    if already_running:
        root.withdraw()
        messagebox.showwarning("程序已经运行", "通用定时图像点击器已经在运行，请查看任务栏。")
        root.destroy()
        return
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    UniversalClickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
