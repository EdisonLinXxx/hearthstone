from __future__ import annotations

import csv
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk


WINDOW_TITLE = "OCR 标注工具"
CONTEXT_IMAGE_MAX_SIZE = (640, 320)

LABEL_MODE_TEXT: dict[str, dict[str, str]] = {
    "mana": {
        "crop_title": "法力裁图",
        "mode_text": "当前模式：full 界面当前法力值/总法力值标注",
        "help_text": "填写完整法力文本，例如：3/3、0/2、9/10",
        "image_max_size": (240, 140),
    },
    "cost": {
        "crop_title": "单卡费用裁图",
        "mode_text": "当前模式：单张卡牌费用标注",
        "help_text": "填写单张卡牌费用数字，例如：0、1、7、10",
        "image_max_size": (520, 520),
    },
}


class OcrLabelerApp:
    def __init__(self, root: tk.Tk, csv_path: Path, label_mode: str) -> None:
        if label_mode not in LABEL_MODE_TEXT:
            raise ValueError(f"Unsupported label mode: {label_mode}")

        self.root = root
        self.csv_path = csv_path
        self.label_mode = label_mode
        self.rows = self._load_rows(csv_path)
        if not self.rows:
            raise RuntimeError(f"No rows found in {csv_path}")
        self.index = self._initial_index()
        self.crop_photo: ImageTk.PhotoImage | None = None
        self.context_photo: ImageTk.PhotoImage | None = None

        self.root.title(f"{WINDOW_TITLE} - {csv_path}")
        self.root.geometry("980x760" if self.label_mode == "mana" else "760x760")

        self.progress_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.crop_path_var = tk.StringVar()
        self.full_path_var = tk.StringVar()
        self.meta_var = tk.StringVar()
        self.mode_var = tk.StringVar()
        self.help_var = tk.StringVar()
        self.label_var = tk.StringVar()

        self._build_ui()
        self._bind_keys()
        self._render_current()

    def _load_rows(self, csv_path: Path) -> list[dict[str, str]]:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _save_rows(self) -> None:
        fieldnames = ["sample_id", "image_path", "full_path", "meta_path", "label", "label_status"]
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.rows)

    def _initial_index(self) -> int:
        for idx, row in enumerate(self.rows):
            if row.get("label_status", "pending") != "done":
                return idx
        return 0

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, textvariable=self.progress_var, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(frame, textvariable=self.mode_var).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(frame, textvariable=self.status_var).pack(anchor=tk.W, pady=(4, 0))
        path_wrap = 940 if self.label_mode == "mana" else 720
        ttk.Label(frame, textvariable=self.crop_path_var, wraplength=path_wrap).pack(anchor=tk.W, pady=(4, 0))
        if self.label_mode == "mana":
            ttk.Label(frame, textvariable=self.full_path_var, wraplength=path_wrap).pack(anchor=tk.W, pady=(2, 0))
        ttk.Label(frame, textvariable=self.meta_var, wraplength=path_wrap).pack(anchor=tk.W, pady=(2, 10))

        image_row = ttk.Frame(frame)
        image_row.pack(fill=tk.BOTH, expand=True, pady=(4, 8))

        crop_frame = ttk.LabelFrame(image_row, text=LABEL_MODE_TEXT[self.label_mode]["crop_title"])
        crop_frame.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True,
            padx=(0, 12 if self.label_mode == "mana" else 0),
        )
        self.crop_image_label = ttk.Label(crop_frame)
        self.crop_image_label.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.context_image_label: ttk.Label | None = None
        if self.label_mode == "mana":
            context_frame = ttk.LabelFrame(image_row, text="完整界面上下文")
            context_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.context_image_label = ttk.Label(context_frame)
            self.context_image_label.pack(padx=8, pady=8)

        entry_row = ttk.Frame(frame)
        entry_row.pack(fill=tk.X, pady=(6, 8))
        ttk.Label(entry_row, text="标签").pack(side=tk.LEFT)
        self.label_entry = ttk.Entry(entry_row, textvariable=self.label_var, width=24)
        self.label_entry.pack(side=tk.LEFT, padx=(8, 12))
        ttk.Label(entry_row, textvariable=self.help_var).pack(side=tk.LEFT)

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(button_row, text="上一张", command=self.prev_row).pack(side=tk.LEFT)
        ttk.Button(button_row, text="保存", command=self.save_only).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_row, text="保存并下一张", command=self.save_and_next).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_row, text="跳过", command=self.skip_row).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_row, text="下一张", command=self.next_row).pack(side=tk.LEFT, padx=(8, 0))

        hint = (
            "快捷键：Enter=保存并下一张，Ctrl+S=保存，Left=上一张，Right=下一张，"
            "Ctrl+K=跳过，Ctrl+Q=退出"
        )
        ttk.Label(frame, text=hint).pack(anchor=tk.W, pady=(12, 0))

    def _bind_keys(self) -> None:
        self.root.bind("<Return>", lambda _event: self.save_and_next())
        self.root.bind("<Control-s>", lambda _event: self.save_only())
        self.root.bind("<Left>", lambda _event: self.prev_row())
        self.root.bind("<Right>", lambda _event: self.next_row())
        self.root.bind("<Control-k>", lambda _event: self.skip_row())
        self.root.bind("<Control-q>", lambda _event: self.root.destroy())

    def _load_photo(self, image_path: Path, max_size: tuple[int, int]) -> ImageTk.PhotoImage:
        image = Image.open(image_path)
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image)

    def _render_current(self) -> None:
        row = self.rows[self.index]
        crop_path = Path(row["image_path"])
        full_path = Path(row["full_path"])
        meta_path = Path(row["meta_path"])

        self.progress_var.set(f"样本 {self.index + 1}/{len(self.rows)}")
        self.mode_var.set(LABEL_MODE_TEXT[self.label_mode]["mode_text"])
        self.help_var.set(LABEL_MODE_TEXT[self.label_mode]["help_text"])
        self.status_var.set(f"状态：{row.get('label_status', 'pending')}  sample_id={row.get('sample_id', '')}")
        self.crop_path_var.set(f"裁图={crop_path}")
        self.full_path_var.set(f"整图={full_path}")
        self.meta_var.set(f"元信息={meta_path}")
        self.label_var.set(row.get("label", ""))

        image_max_size = LABEL_MODE_TEXT[self.label_mode]["image_max_size"]
        self.crop_photo = self._load_photo(crop_path, image_max_size)
        self.crop_image_label.configure(image=self.crop_photo)
        if self.context_image_label is not None:
            self.context_photo = self._load_photo(full_path, CONTEXT_IMAGE_MAX_SIZE)
            self.context_image_label.configure(image=self.context_photo)

        self.label_entry.focus_set()
        self.label_entry.selection_range(0, tk.END)

    def save_only(self) -> None:
        row = self.rows[self.index]
        row["label"] = self.label_var.get().strip()
        row["label_status"] = "done" if row["label"] else "pending"
        self._save_rows()
        self._render_current()

    def save_and_next(self) -> None:
        self.save_only()
        self.next_row()

    def skip_row(self) -> None:
        row = self.rows[self.index]
        row["label"] = self.label_var.get().strip()
        row["label_status"] = "skip"
        self._save_rows()
        self.next_row()

    def prev_row(self) -> None:
        if self.index > 0:
            self.index -= 1
            self._render_current()

    def next_row(self) -> None:
        if self.index < len(self.rows) - 1:
            self.index += 1
            self._render_current()
            return
        messagebox.showinfo(WINDOW_TITLE, "已经是最后一张样本。")


def run_ocr_labeler(csv_path: Path, label_mode: str) -> int:
    csv_path = csv_path.resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    root = tk.Tk()
    app = OcrLabelerApp(root, csv_path, label_mode=label_mode)
    del app
    root.mainloop()
    return 0
