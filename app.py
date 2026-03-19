import os
import re
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageDraw, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD
import threading
import random
import numpy as np

Image.MAX_IMAGE_PIXELS = None

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
selected_files = []
watermark_path = None

# -----------------------------
# Watermark UI / preview state
# -----------------------------
watermark_size_slider = None
count_slider = None
opacity_slider = None

manual_watermark_enabled = None
manual_positions_by_path = {}  # dict[path -> list[(x_rel, y_rel)]], per-image manual positions

watermark_preview_canvas = None
_manual_preview_state = {}

preview_image_index = None
preview_image_picker = None
preview_image_label = None
current_status_state = "ready"  # ready | processing | done


# -----------------------------
# Helpers
# -----------------------------
def sanitize_filename(name):
    name = name.strip()
    return re.sub(r'[<>:"/\\|?*]', "", name)


def format_bytes(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"

def apply_smart_watermark(
    img,
    watermark_file,
    count,
    opacity,
    size_fraction=0.12,
    manual_positions_rel=None,
    rng=None,
):
    """
    Places watermarks inside the photo's estimated "subject" area.

    - Watermark size is controlled by `size_fraction` as a fraction of the image
      width (fixed; not random).
    - If `manual_positions_rel=[(x_rel, y_rel), ...]` is provided, one watermark
      is placed at each relative position (top-left) on the *resized* image.
    - If `rng` is provided, placement becomes deterministic (useful for previews).
    """
    if not watermark_file:
        return img

    try:
        watermark = Image.open(watermark_file).convert("RGBA")
    except Exception:
        return img

    base = img.convert("RGBA")
    rng = rng or random
    opacity = float(opacity)
    size_fraction = float(size_fraction)
    w, h = base.size
    watermark_width, watermark_height = watermark.size

    if w <= 0 or h <= 0 or watermark_width <= 0 or watermark_height <= 0:
        return base

    # ---------- Estimate subject mask (heuristic; no ML dependencies) ----------
    # We treat the corner color as "background" and mark pixels that differ enough
    # as "subject". This generally works well for photos where the background is
    # relatively uniform.
    rgb = np.array(base.convert("RGB"), dtype=np.float32)
    corner = max(10, int(min(w, h) * 0.06))
    # Sample four corner rectangles.
    tl = rgb[0:corner, 0:corner, :]
    tr = rgb[0:corner, w - corner:w, :]
    bl = rgb[h - corner:h, 0:corner, :]
    br = rgb[h - corner:h, w - corner:w, :]
    bg_color = np.median(np.concatenate([tl.reshape(-1, 3), tr.reshape(-1, 3), bl.reshape(-1, 3), br.reshape(-1, 3)]), axis=0)

    dist = np.sqrt(((rgb - bg_color) ** 2).sum(axis=2))  # (h, w)
    flat = dist.reshape(-1)

    # Pick a threshold that yields a reasonable amount of "subject" pixels.
    # If the image is complex and fails, we fall back to a conservative threshold.
    subject_mask = None
    for perc in (70, 75, 80, 82, 84, 86, 88, 90, 92, 94):
        t = np.percentile(flat, perc)
        m = dist > t
        ratio = float(m.mean())
        if 0.05 <= ratio <= 0.6:
            subject_mask = m
            break
    if subject_mask is None:
        subject_mask = dist > np.percentile(flat, 85)

    ys, xs = np.where(subject_mask)
    safe_zone_margin = int(min(w, h) * 0.05)
    safe_zone_margin = max(10, min(safe_zone_margin, 80))

    # Convert mask to a bounding box; if it's too small, use centered safe zone.
    if xs.size > 0:
        xmin, xmax = int(xs.min()), int(xs.max())
        ymin, ymax = int(ys.min()), int(ys.max())
        pad = int(min(w, h) * 0.02)
        xmin = max(0, xmin - pad)
        ymin = max(0, ymin - pad)
        xmax = min(w - 1, xmax + pad)
        ymax = min(h - 1, ymax + pad)

        subject_area_ratio = ((xmax - xmin + 1) * (ymax - ymin + 1)) / float(w * h)
        if subject_area_ratio < 0.10:
            xmin, xmax = safe_zone_margin, w - safe_zone_margin - 1
            ymin, ymax = safe_zone_margin, h - safe_zone_margin - 1
    else:
        xmin, xmax = safe_zone_margin, w - safe_zone_margin - 1
        ymin, ymax = safe_zone_margin, h - safe_zone_margin - 1

    # ---------- Watermark placement ----------
    placed = []  # list of (x, y, wm_w, wm_h)
    result = base.copy()

    # Keep watermarks away from each other by expanding their "exclusion" bounds.
    min_gap = int(min(w, h) * 0.04)
    min_gap = max(8, min(min_gap, 60))

    # Require that a good portion of the watermark area lies within the subject.
    # (Higher = less likely to spill into background.)
    min_subject_coverage = 0.60

    max_attempts_per_watermark = 250

    # Clamp count to something sensible.
    try:
        count = int(count)
    except Exception:
        count = 1
    count = max(1, min(count, 25))

    # Compute fixed watermark size as a % of image width. This makes the UI slider
    # predictable and prevents the watermark from becoming effectively invisible
    # when the source watermark image is huge.
    min_w_frac = 0.03
    max_w_frac = 0.28
    target_w_frac = max(min_w_frac, min(size_fraction, max_w_frac))

    wm_w = int(max(2, w * target_w_frac))
    aspect = watermark_height / float(watermark_width)
    wm_h = int(max(2, wm_w * aspect))

    # Also cap height to keep it reasonable.
    max_h_frac = 0.28
    wm_h = min(wm_h, int(max(2, h * max_h_frac)))

    # Avoid ever producing a 0-sized watermark when the target image is tiny.
    wm_w = max(1, min(wm_w, w - 1))
    wm_h = max(1, min(wm_h, h - 1))

    if wm_w < 1 or wm_h < 1:
        return base

    wm_resized = watermark.resize((wm_w, wm_h))
    alpha = wm_resized.split()[3]
    alpha = alpha.point(lambda p: int(p * float(opacity)))
    wm_resized.putalpha(alpha)

    # ---------- Manual placement (multiple pinned watermarks) ----------
    if manual_positions_rel:
        def subject_coverage_ok(x_try, y_try):
            region = subject_mask[y_try:y_try + wm_h, x_try:x_try + wm_w]
            if region.size == 0:
                return False
            return float(region.mean()) >= min_subject_coverage

        for (x_rel, y_rel) in manual_positions_rel:
            x_rel = min(1.0, max(0.0, float(x_rel)))
            y_rel = min(1.0, max(0.0, float(y_rel)))

            x = int(x_rel * w)
            y = int(y_rel * h)

            # Clamp so the watermark fully fits inside the subject bbox.
            x = max(xmin, min(x, xmax - wm_w + 1))
            y = max(ymin, min(y, ymax - wm_h + 1))

            if not subject_coverage_ok(x, y):
                # Limited local search around the user's chosen spot.
                best = None
                best_dist = None
                manual_x, manual_y = x, y
                radius = max(20, int(min(w, h) * 0.08))

                for _attempt in range(120):
                    x_try = max(xmin, min(xmax - wm_w + 1, manual_x + int(rng.uniform(-radius, radius))))
                    y_try = max(ymin, min(ymax - wm_h + 1, manual_y + int(rng.uniform(-radius, radius))))
                    if subject_coverage_ok(x_try, y_try):
                        d = abs(x_try - manual_x) + abs(y_try - manual_y)
                        if best is None or d < best_dist:
                            best = (x_try, y_try)
                            best_dist = d
                            if best_dist == 0:
                                break
                if best is not None:
                    x, y = best

            result.paste(wm_resized, (x, y), wm_resized)

        return result

    # ---------- Auto placement ----------
    for _ in range(count):
        placed_this = False

        for _attempt in range(max_attempts_per_watermark):
            x_low = xmin
            x_high = max(x_low, xmax - wm_w + 1)
            y_low = ymin
            y_high = max(y_low, ymax - wm_h + 1)

            x = rng.randint(x_low, x_high)
            y = rng.randint(y_low, y_high)

            region = subject_mask[y:y + wm_h, x:x + wm_w]
            if region.size == 0 or float(region.mean()) < min_subject_coverage:
                continue

            # Enforce spacing between watermarks.
            overlap = False
            for (px, py, pw, ph) in placed:
                if not (
                    x + wm_w + min_gap < px
                    or x > px + pw + min_gap
                    or y + wm_h + min_gap < py
                    or y > py + ph + min_gap
                ):
                    overlap = True
                    break
            if overlap:
                continue

            result.paste(wm_resized, (x, y), wm_resized)
            placed.append((x, y, wm_w, wm_h))
            placed_this = True
            break

        if not placed_this:
            break

    return result


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def parse_dropped_files(data):
    files = root.tk.splitlist(data)
    valid_files = []

    for file_path in files:
        path = Path(file_path)
        if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
            valid_files.append(str(path))

    return valid_files



# -----------------------------
# Theme
# -----------------------------
LIGHT_THEME = {
    "BG": "#eef2f8",
    "CARD": "#ffffff",
    "CARD_2": "#f4f7fd",
    "TEXT": "#0b1220",
    "MUTED": "#5c6a84",
    "ACCENT": "#4f46e5",
    "ACCENT_HOVER": "#4338ca",
    "BORDER": "#d9e2f2",
    "INPUT_BG": "#ffffff",
    "LIST_BG": "#f8faff",
    "DROP_BG": "#f5f8ff",
    "RESULT_BG": "#f6f8fd",
    "PROGRESS_TROUGH": "#d6deef",
    "BUTTON_TEXT_ON_ACCENT": "#ffffff",
    "BUTTON_TEXT_ON_CARD": "#0b1220",
    "BUTTON_DISABLED_BG": "#e4e9f4",
    "BUTTON_DISABLED_TEXT": "#97a3b8",
    "CHIP_READY_BG": "#e8eefc",
    "CHIP_READY_FG": "#31477a",
    "CHIP_PROCESSING_BG": "#ede9fe",
    "CHIP_PROCESSING_FG": "#4c1d95",
    "CHIP_DONE_BG": "#dcfce7",
    "CHIP_DONE_FG": "#166534",
}

DARK_THEME = {
    "BG": "#0b1020",
    "CARD": "#111a2e",
    "CARD_2": "#1a2742",
    "TEXT": "#f1f5ff",
    "MUTED": "#95a3bd",
    "ACCENT": "#6366f1",
    "ACCENT_HOVER": "#4f46e5",
    "BORDER": "#2a3a59",
    "INPUT_BG": "#0f172a",
    "LIST_BG": "#0f172a",
    "DROP_BG": "#0f172a",
    "RESULT_BG": "#0f172a",
    "PROGRESS_TROUGH": "#263552",
    "BUTTON_TEXT_ON_ACCENT": "#ffffff",
    "BUTTON_TEXT_ON_CARD": "#f1f5ff",
    "BUTTON_DISABLED_BG": "#2c3750",
    "BUTTON_DISABLED_TEXT": "#738099",
    "CHIP_READY_BG": "#1f2a45",
    "CHIP_READY_FG": "#b8c7e6",
    "CHIP_PROCESSING_BG": "#31265c",
    "CHIP_PROCESSING_FG": "#d6c9ff",
    "CHIP_DONE_BG": "#18382a",
    "CHIP_DONE_FG": "#8ce0b2",
}

theme_var = None
theme = LIGHT_THEME


# -----------------------------
# Custom Button
# -----------------------------
class CustomButton(tk.Frame):
    def __init__(
        self,
        parent,
        text,
        command,
        width=None,
        fill_x=False,
        variant="secondary",
        parent_bg=None
    ):
        super().__init__(parent, bd=0, highlightthickness=0)
        self.command = command
        self.text = text
        self.variant = variant
        self.enabled = True
        self.fill_x = fill_x
        self.parent_bg = parent_bg if parent_bg is not None else parent.cget("bg")

        self.outer = tk.Frame(self, bd=0, highlightthickness=1)
        self.outer.pack(fill="x" if fill_x else "none", expand=fill_x)

        self.label = tk.Label(
            self.outer,
            text=text,
            font=("Segoe UI", 10 if variant == "secondary" else 11, "bold"),
            padx=18,
            pady=12 if variant == "primary" else 10
        )
        self.label.pack(fill="x" if fill_x else "both", expand=fill_x)

        if width is not None and not fill_x:
            self.label.config(width=width)

        for widget in (self, self.outer, self.label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<ButtonRelease-1>", self._on_release)

        self._hover = False
        self._pressed = False
        self.apply_theme()

    def set_parent_bg(self, color):
        self.parent_bg = color
        self.configure(bg=color)

    def apply_theme(self):
        self.configure(bg=self.parent_bg)

        if self.variant == "primary":
            self.base_bg = theme["ACCENT"]
            self.hover_bg = theme["ACCENT_HOVER"]
            self.press_bg = theme["ACCENT_HOVER"]
            self.text_color = theme["BUTTON_TEXT_ON_ACCENT"]
            self.border_color = theme["ACCENT"]
        else:
            self.base_bg = theme["CARD_2"]
            self.hover_bg = theme["BORDER"]
            self.press_bg = theme["BORDER"]
            self.text_color = theme["BUTTON_TEXT_ON_CARD"]
            self.border_color = theme["BORDER"]

        self.disabled_bg = theme["BUTTON_DISABLED_BG"]
        self.disabled_text = theme["BUTTON_DISABLED_TEXT"]

        self._render()

    def _render(self):
        if not self.enabled:
            bg = self.disabled_bg
            fg = self.disabled_text
            border = theme["BORDER"]
        else:
            if self._pressed:
                bg = self.press_bg
            elif self._hover:
                bg = self.hover_bg
            else:
                bg = self.base_bg
            fg = self.text_color
            border = self.border_color

        self.outer.configure(bg=border, highlightbackground=border)
        self.label.configure(bg=bg, fg=fg)

    def _on_enter(self, event=None):
        if not self.enabled:
            return
        self._hover = True
        self._render()

    def _on_leave(self, event=None):
        if not self.enabled:
            return
        self._hover = False
        self._pressed = False
        self._render()

    def _on_click(self, event=None):
        if not self.enabled:
            return
        self._pressed = True
        self._render()

    def _on_release(self, event=None):
        if not self.enabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        self._render()

        if was_pressed and self.command:
            try:
                x_root = event.x_root if event else None
                y_root = event.y_root if event else None
                if x_root is not None and y_root is not None:
                    widget_under_pointer = self.winfo_containing(x_root, y_root)
                    if widget_under_pointer not in (self, self.outer, self.label):
                        return
            except Exception:
                pass

            self.command()

    def set_enabled(self, enabled=True):
        self.enabled = enabled
        self._hover = False
        self._pressed = False
        self._render()


def current_theme():
    return DARK_THEME if theme_var.get() else LIGHT_THEME


def apply_theme():
    global theme
    theme = current_theme()

    root.configure(bg=theme["BG"])
    main_container.configure(bg=theme["BG"])
    header_frame.configure(bg=theme["BG"])
    header_left.configure(bg=theme["BG"])
    app_title.configure(bg=theme["BG"], fg=theme["TEXT"])
    app_subtitle.configure(bg=theme["BG"], fg=theme["MUTED"])
    theme_toggle_wrap.configure(bg=theme["BG"])
    theme_label.configure(bg=theme["BG"], fg=theme["MUTED"])

    style.configure(
        "Modern.Horizontal.TProgressbar",
        troughcolor=theme["PROGRESS_TROUGH"],
        background=theme["ACCENT"],
        bordercolor=theme["PROGRESS_TROUGH"],
        lightcolor=theme["ACCENT"],
        darkcolor=theme["ACCENT"],
        thickness=16
    )

    style.configure(
        "Modern.TCombobox",
        fieldbackground=theme["INPUT_BG"],
        background=theme["CARD_2"],
        foreground=theme["TEXT"],
        arrowcolor=theme["TEXT"],
        bordercolor=theme["BORDER"],
        lightcolor=theme["BORDER"],
        darkcolor=theme["BORDER"]
    )

    style.map(
        "Modern.TCombobox",
        fieldbackground=[("readonly", theme["INPUT_BG"])],
        foreground=[("readonly", theme["TEXT"])],
    )

    style.configure(
        "Modern.TPanedwindow",
        background=theme["BG"],
        sashrelief="flat",
        sashwidth=8,
    )

    content_frame.configure(bg=theme["BG"])
    if "split_pane" in globals() and split_pane is not None:
        split_pane.configure(style="Modern.TPanedwindow")

    for panel in [left_panel, right_panel]:
        panel.configure(bg=theme["CARD"], highlightbackground=theme["BORDER"])

    if "left_canvas" in globals() and left_canvas is not None:
        left_canvas.configure(bg=theme["CARD"])
    if "right_canvas" in globals() and right_canvas is not None:
        right_canvas.configure(bg=theme["CARD"])

    for frame in [left_inner, right_inner, button_row, files_section, file_list_frame,
                  progress_wrap, action_row]:
        frame.configure(bg=theme["CARD"])

    preview_section.configure(bg=theme["CARD_2"], highlightbackground=theme["BORDER"])
    preview_frame.configure(bg=theme["CARD_2"])
    if "watermark_preview_card" in globals() and watermark_preview_card is not None:
        watermark_preview_card.configure(bg=theme["CARD_2"], highlightbackground=theme["BORDER"])

    for widget in [
        left_title, left_desc, selected_count_label, files_title,
        right_title, right_desc, preview_title, progress_text_label
    ]:
        widget.configure(
            bg=theme["CARD"],
            fg=theme["TEXT"] if widget in [
                left_title, selected_count_label, files_title,
                right_title, preview_title
            ] else theme["MUTED"]
        )
    preview_title.configure(bg=theme["CARD_2"], fg=theme["TEXT"])

    drop_frame.configure(bg=theme["DROP_BG"], highlightbackground=theme["BORDER"])
    drop_title.configure(bg=theme["DROP_BG"], fg=theme["TEXT"])
    drop_subtitle.configure(bg=theme["DROP_BG"], fg=theme["MUTED"])

    if "pill_badge" in globals() and pill_badge is not None:
        pill_badge.configure(bg=theme["CARD_2"], fg=theme["ACCENT"])

    settings_card.configure(bg=theme["DROP_BG"], highlightbackground=theme["BORDER"])
    results_box.configure(bg=theme["RESULT_BG"], highlightbackground=theme["BORDER"])

    for lbl in settings_labels:
        lbl.configure(bg=theme["DROP_BG"], fg=theme["MUTED"])

    for lbl in result_key_labels:
        lbl.configure(bg=theme["RESULT_BG"], fg=theme["MUTED"])
    for lbl in result_value_labels:
        lbl.configure(bg=theme["RESULT_BG"], fg=theme["TEXT"])

    # Watermark preview widgets (may not exist during initial import)
    if "watermark_preview_canvas" in globals() and watermark_preview_canvas is not None:
        watermark_preview_canvas.configure(
            bg=theme["INPUT_BG"],
            highlightbackground=theme["BORDER"],
        )
    if "watermark_preview_title" in globals() and watermark_preview_title is not None:
        watermark_preview_title.configure(bg=theme["CARD_2"], fg=theme["TEXT"])
    if "watermark_preview_hint" in globals() and watermark_preview_hint is not None:
        watermark_preview_hint.configure(bg=theme["CARD_2"], fg=theme["MUTED"])
    if "preview_picker_row" in globals() and preview_picker_row is not None:
        preview_picker_row.configure(bg=theme["CARD_2"])
    if "preview_picker_label" in globals() and preview_picker_label is not None:
        preview_picker_label.configure(bg=theme["CARD_2"], fg=theme["MUTED"])
    if "preview_image_label" in globals() and preview_image_label is not None:
        preview_image_label.configure(bg=theme["CARD_2"], fg=theme["MUTED"])

    style_entry(width_entry)
    style_entry(height_entry)
    style_entry(rename_entry)
    style_entry(start_number_entry)

    style_listbox(file_listbox)
    style_listbox(preview_listbox)

    select_button.set_parent_bg(theme["CARD"])
    clear_button.set_parent_bg(theme["CARD"])
    optimize_button.set_parent_bg(theme["CARD"])
    select_button.apply_theme()
    clear_button.apply_theme()
    optimize_button.apply_theme()

    theme_toggle.configure(
        bg=theme["BG"],
        activebackground=theme["BG"],
        selectcolor=theme["BG"],
        fg=theme["TEXT"]
    )

    # Keep native Tk widgets visually aligned with the modern theme.
    for cb in [toggle, manual_toggle]:
        cb.configure(
            bg=theme["CARD"],
            fg=theme["TEXT"],
            activebackground=theme["CARD"],
            activeforeground=theme["TEXT"],
            selectcolor=theme["CARD"],
            highlightthickness=0,
        )

    for slider in [opacity_slider, watermark_size_slider, count_slider]:
        slider.configure(
            bg=theme["CARD"],
            fg=theme["TEXT"],
            activebackground=theme["ACCENT_HOVER"],
            troughcolor=theme["CARD_2"],
            highlightthickness=0,
            bd=0,
        )

    set_status_chip(current_status_state)


# -----------------------------
# Widget styling helpers
# -----------------------------
def style_entry(entry):
    entry.configure(
        bg=theme["INPUT_BG"],
        fg=theme["TEXT"],
        insertbackground=theme["TEXT"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=theme["BORDER"],
        highlightcolor=theme["ACCENT"]
    )


def style_listbox(lb):
    lb.configure(
        bg=theme["LIST_BG"],
        fg=theme["TEXT"],
        selectbackground=theme["ACCENT"],
        selectforeground="white",
        relief="flat",
        highlightthickness=1,
        highlightbackground=theme["BORDER"],
        bd=0
    )


# -----------------------------
# UI update functions
# -----------------------------
def update_selected_label():
    count = len(selected_files)
    if count == 0:
        selected_count_label.config(text="No images selected")
    elif count == 1:
        selected_count_label.config(text="1 image selected")
    else:
        selected_count_label.config(text=f"{count} images selected")


def update_preview(*args):
    preview_listbox.delete(0, tk.END)

    if not selected_files:
        preview_listbox.insert(tk.END, "Your renamed optimized files will appear here.")
        return

    base_name = sanitize_filename(rename_entry.get())
    if not base_name:
        preview_listbox.insert(tk.END, "Enter a base file name to preview output names.")
        return

    try:
        start_number = int(start_number_entry.get())
        if start_number < 1:
            raise ValueError
    except ValueError:
        preview_listbox.insert(tk.END, "Starting number must be 1 or higher.")
        return

    output_format = format_var.get().lower()
    preview_limit = 100

    for index, _ in enumerate(selected_files[:preview_limit], start=start_number):
        preview_listbox.insert(tk.END, f"{base_name}-{index}.{output_format}")

    if len(selected_files) > preview_limit:
        preview_listbox.insert(tk.END, f"... and {len(selected_files) - preview_limit} more")


def refresh_file_table():
    file_listbox.delete(0, tk.END)

    if not selected_files:
        file_listbox.insert(tk.END, "No files loaded yet.")
        return

    for file_path in selected_files:
        file_listbox.insert(tk.END, os.path.basename(file_path))

    refresh_preview_image_picker()


# -----------------------------
# Watermark preview + manual drag
# -----------------------------
_PREVIEW_MAX_W = 560
_PREVIEW_MAX_H = 360


def _estimate_subject_mask_and_bbox(base_rgba):
    """
    Returns (subject_mask, xmin, xmax, ymin, ymax).
    subject_mask is a boolean numpy array aligned with base_rgba pixels.
    """
    rgb = np.array(base_rgba.convert("RGB"), dtype=np.float32)
    w, h = base_rgba.size

    corner = max(10, int(min(w, h) * 0.06))
    tl = rgb[0:corner, 0:corner, :]
    tr = rgb[0:corner, w - corner:w, :]
    bl = rgb[h - corner:h, 0:corner, :]
    br = rgb[h - corner:h, w - corner:w, :]
    bg_color = np.median(
        np.concatenate([tl.reshape(-1, 3), tr.reshape(-1, 3), bl.reshape(-1, 3), br.reshape(-1, 3)]),
        axis=0,
    )

    dist = np.sqrt(((rgb - bg_color) ** 2).sum(axis=2))  # (h, w)
    flat = dist.reshape(-1)

    subject_mask = None
    for perc in (70, 75, 80, 82, 84, 86, 88, 90, 92, 94):
        t = np.percentile(flat, perc)
        m = dist > t
        ratio = float(m.mean())
        if 0.05 <= ratio <= 0.6:
            subject_mask = m
            break
    if subject_mask is None:
        subject_mask = dist > np.percentile(flat, 85)

    ys, xs = np.where(subject_mask)
    safe_zone_margin = int(min(w, h) * 0.05)
    safe_zone_margin = max(10, min(safe_zone_margin, 80))

    if xs.size > 0:
        xmin, xmax = int(xs.min()), int(xs.max())
        ymin, ymax = int(ys.min()), int(ys.max())
        pad = int(min(w, h) * 0.02)
        xmin = max(0, xmin - pad)
        ymin = max(0, ymin - pad)
        xmax = min(w - 1, xmax + pad)
        ymax = min(h - 1, ymax + pad)

        subject_area_ratio = ((xmax - xmin + 1) * (ymax - ymin + 1)) / float(w * h)
        if subject_area_ratio < 0.10:
            xmin, xmax = safe_zone_margin, w - safe_zone_margin - 1
            ymin, ymax = safe_zone_margin, h - safe_zone_margin - 1
    else:
        xmin, xmax = safe_zone_margin, w - safe_zone_margin - 1
        ymin, ymax = safe_zone_margin, h - safe_zone_margin - 1

    return subject_mask, xmin, xmax, ymin, ymax


def update_watermark_preview(*_args):
    """
    Live preview for how watermark placement will look.
    - Auto mode: renders a composite image preview.
    - Manual mode: renders a draggable watermark overlay on top of the image.
    """
    global manual_positions_by_path, _manual_preview_state

    if watermark_preview_canvas is None:
        return

    watermark_preview_canvas.delete("all")
    _manual_preview_state.clear()

    if not selected_files:
        watermark_preview_canvas.create_text(
            8,
            8,
            anchor="nw",
            text="Select images to preview watermark placement.",
            fill=theme["MUTED"] if "theme" in globals() else "gray",
            font=("Segoe UI", 10),
        )
        return

    # Read the same resize dimensions as the optimizer.
    try:
        max_w = int(width_entry.get())
        max_h = int(height_entry.get())
    except Exception:
        max_w, max_h = 800, 600

    idx = 0
    try:
        if preview_image_index is not None:
            idx = int(preview_image_index.get())
    except Exception:
        idx = 0
    if idx < 0:
        idx = 0
    if idx >= len(selected_files):
        idx = len(selected_files) - 1
        if preview_image_index is not None:
            try:
                preview_image_index.set(idx)
            except Exception:
                pass

    sample_path = selected_files[idx]
    try:
        with Image.open(sample_path) as im:
            im.thumbnail((max_w, max_h), Image.LANCZOS)
            target_rgba = im.convert("RGBA")
    except Exception:
        watermark_preview_canvas.create_text(
            8,
            8,
            anchor="nw",
            text="Failed to load preview image.",
            fill=theme["MUTED"] if "theme" in globals() else "gray",
            font=("Segoe UI", 10),
        )
        return

    target_w, target_h = target_rgba.size

    # Build a display thumbnail for the UI canvas.
    display_img = target_rgba.copy()
    display_img.thumbnail((_PREVIEW_MAX_W, _PREVIEW_MAX_H), Image.LANCZOS)
    disp_w, disp_h = display_img.size

    # Map target coordinates -> canvas coordinates (top-left anchored at (0,0)).
    disp_scale_x = disp_w / float(target_w)
    disp_scale_y = disp_h / float(target_h)

    base_photo = ImageTk.PhotoImage(display_img)
    watermark_preview_canvas.create_image(0, 0, anchor="nw", image=base_photo)
    watermark_preview_canvas._base_photo_ref = base_photo  # prevent GC

    if not watermark_enabled.get() or not watermark_path:
        return

    # If manual placement is on, keep per-image manual positions list sized to the slider.
    if manual_watermark_enabled is not None and manual_watermark_enabled.get():
        desired = 1
        try:
            desired = int(count_slider.get()) if count_slider is not None else 1
        except Exception:
            desired = 1
        desired = max(1, min(desired, 10))
        positions = manual_positions_by_path.get(sample_path, [])
        if len(positions) < desired:
            positions = positions + [(0.5, 0.5)] * (desired - len(positions))
        elif len(positions) > desired:
            positions = positions[:desired]
        manual_positions_by_path[sample_path] = positions

    size_fraction = watermark_size_slider.get() if watermark_size_slider is not None else 0.12
    opacity = opacity_slider.get() if opacity_slider is not None else 0.3
    manual_on = manual_watermark_enabled is not None and manual_watermark_enabled.get()

    # Manual mode: draggable single watermark overlay.
    if manual_on:
        try:
            watermark_img = Image.open(watermark_path).convert("RGBA")
        except Exception:
            return

        # Subject mask + bbox for clamping / coverage checks.
        subject_mask, xmin, xmax, ymin, ymax = _estimate_subject_mask_and_bbox(target_rgba)

        # Compute fixed watermark size with the same caps used for processing.
        watermark_width, watermark_height = watermark_img.size
        min_scale = 0.03
        max_w_frac = 0.28
        target_w_frac = max(min_scale, min(size_fraction, max_w_frac))
        wm_w = int(max(2, target_w * target_w_frac))
        aspect = watermark_height / float(watermark_width)
        wm_h = int(max(2, wm_w * aspect))
        wm_h = min(wm_h, int(max(2, target_h * 0.28)))
        # Avoid ever producing a 0-sized watermark when the target image is tiny.
        wm_w = max(1, min(wm_w, target_w - 1))
        wm_h = max(1, min(wm_h, target_h - 1))

        if wm_w < 1 or wm_h < 1:
            return

        wm_resized = watermark_img.resize((wm_w, wm_h))
        alpha = wm_resized.split()[3]
        alpha = alpha.point(lambda p: int(p * float(opacity)))
        wm_resized.putalpha(alpha)

        # Convert watermark to display scale for the canvas.
        wm_disp_w = max(2, int(round(wm_w * disp_scale_x)))
        wm_disp_h = max(2, int(round(wm_h * disp_scale_y)))
        wm_disp = wm_resized.resize((wm_disp_w, wm_disp_h), Image.LANCZOS)
        wm_photo = ImageTk.PhotoImage(wm_disp)
        watermark_preview_canvas._wm_photo_ref = wm_photo  # prevent GC

        def subject_coverage_ok(x_try, y_try):
            region = subject_mask[y_try:y_try + wm_h, x_try:x_try + wm_w]
            if region.size == 0:
                return False
            return float(region.mean()) >= 0.60

        desired_count = 1
        try:
            desired_count = int(count_slider.get()) if count_slider is not None else 1
        except Exception:
            desired_count = 1
        desired_count = max(1, min(desired_count, 10))

        positions = manual_positions_by_path.get(sample_path, [])

        # Ensure we have N manual positions for this image.
        if len(positions) < desired_count:
            cx = xmin + (xmax - xmin + 1 - wm_w) // 2
            cy = ymin + (ymax - ymin + 1 - wm_h) // 2
            for i in range(len(positions), desired_count):
                off = int((i % 3) * wm_w * 0.35)
                x_try = max(xmin, min(cx + off, xmax - wm_w + 1))
                y_try = max(ymin, min(cy + int((i // 3) * wm_h * 0.35), ymax - wm_h + 1))
                if not subject_coverage_ok(x_try, y_try):
                    rng_local = random.Random(i + 7)
                    for _attempt in range(200):
                        x_cand = rng_local.randint(xmin, max(xmin, xmax - wm_w + 1))
                        y_cand = rng_local.randint(ymin, max(ymin, ymax - wm_h + 1))
                        if subject_coverage_ok(x_cand, y_cand):
                            x_try, y_try = x_cand, y_cand
                            break
                positions.append((x_try / float(target_w), y_try / float(target_h)))

        if len(positions) > desired_count:
            positions = positions[:desired_count]

        manual_positions_by_path[sample_path] = positions

        # Store preview state for drag operations.
        _manual_preview_state.update(
            {
                "target_w": target_w,
                "target_h": target_h,
                "disp_scale_x": disp_scale_x,
                "disp_scale_y": disp_scale_y,
                "subject_mask": subject_mask,
                "bbox": (xmin, xmax, ymin, ymax),
                "wm_w": wm_w,
                "wm_h": wm_h,
                "wm_disp_w": wm_disp_w,
                "wm_disp_h": wm_disp_h,
                "wm_photo": wm_photo,
                "dragging": False,
                "drag_dx": 0,
                "drag_dy": 0,
                "min_subject_coverage": 0.60,
                "active_index": None,
                "file_path": sample_path,
            }
        )

        # Draw N draggable watermark overlays.
        items = []
        for i, (xr, yr) in enumerate(manual_positions_by_path.get(sample_path, [])):
            x_try = int(xr * target_w)
            y_try = int(yr * target_h)
            x_try = max(xmin, min(x_try, xmax - wm_w + 1))
            y_try = max(ymin, min(y_try, ymax - wm_h + 1))
            if not subject_coverage_ok(x_try, y_try):
                # Keep it where it was; user can drag again.
                pass
            canvas_x = x_try * disp_scale_x
            canvas_y = y_try * disp_scale_y
            wm_item = watermark_preview_canvas.create_image(
                canvas_x,
                canvas_y,
                anchor="nw",
                image=wm_photo,
                tags=("wm", f"wm_idx_{i}"),
            )
            items.append(wm_item)

        _manual_preview_state["wm_items"] = items

        watermark_preview_canvas.tag_bind("wm", "<ButtonPress-1>", _on_wm_drag_start)
        watermark_preview_canvas.tag_bind("wm", "<B1-Motion>", _on_wm_drag_motion)
        watermark_preview_canvas.tag_bind("wm", "<ButtonRelease-1>", _on_wm_drag_release)
        return

    # Auto mode: render a composite preview deterministically (stable layout).
    count_preview = int(1 if manual_on else count_slider.get()) if count_slider is not None else 1
    rng_preview = random.Random(hash((sample_path, size_fraction, opacity, count_preview, target_w, target_h)) & 0xFFFFFFFF)
    try:
        composite = apply_smart_watermark(
            target_rgba,
            watermark_path,
            count_preview,
            opacity,
            size_fraction=size_fraction,
            rng=rng_preview,
        )
        composite_img = composite.copy()
        composite_img.thumbnail((_PREVIEW_MAX_W, _PREVIEW_MAX_H), Image.LANCZOS)
        composite_photo = ImageTk.PhotoImage(composite_img)
        watermark_preview_canvas.create_image(0, 0, anchor="nw", image=composite_photo)
        watermark_preview_canvas._composite_photo_ref = composite_photo
    except Exception:
        # If preview fails, fall back to base image already drawn.
        return

    # Update label (if present) for nicer UX.
    if preview_image_label is not None:
        try:
            preview_image_label.config(text=os.path.basename(sample_path))
        except Exception:
            pass


def refresh_preview_image_picker():
    if preview_image_picker is None or preview_image_index is None:
        return
    names = [os.path.basename(p) for p in selected_files] if selected_files else []
    try:
        preview_image_picker.configure(values=names)
    except Exception:
        return

    if not names:
        try:
            preview_image_index.set(0)
        except Exception:
            pass
        try:
            preview_image_picker.set("")
        except Exception:
            pass
        if preview_image_label is not None:
            preview_image_label.config(text="-")
        return

    idx = int(preview_image_index.get() or 0)
    idx = max(0, min(idx, len(names) - 1))
    preview_image_index.set(idx)
    try:
        preview_image_picker.current(idx)
    except Exception:
        try:
            preview_image_picker.set(names[idx])
        except Exception:
            pass
    if preview_image_label is not None:
        preview_image_label.config(text=names[idx])


def on_preview_image_selected(_event=None):
    if preview_image_picker is None or preview_image_index is None:
        return
    val = preview_image_picker.get()
    if not val or not selected_files:
        return
    # Find first matching basename.
    for i, p in enumerate(selected_files):
        if os.path.basename(p) == val:
            preview_image_index.set(i)
            break
    update_watermark_preview()


def preview_prev_image():
    if preview_image_index is None or not selected_files:
        return
    idx = int(preview_image_index.get() or 0) - 1
    if idx < 0:
        idx = len(selected_files) - 1
    preview_image_index.set(idx)
    refresh_preview_image_picker()
    update_watermark_preview()


def preview_next_image():
    if preview_image_index is None or not selected_files:
        return
    idx = int(preview_image_index.get() or 0) + 1
    if idx >= len(selected_files):
        idx = 0
    preview_image_index.set(idx)
    refresh_preview_image_picker()
    update_watermark_preview()


def _on_wm_drag_start(event):
    st = _manual_preview_state
    if not st:
        return
    canvas = watermark_preview_canvas
    current = canvas.find_withtag("current")
    if not current:
        return
    wm_item = current[0]
    tags = canvas.gettags(wm_item)
    idx = None
    for t in tags:
        if t.startswith("wm_idx_"):
            try:
                idx = int(t.replace("wm_idx_", ""))
            except Exception:
                idx = None
            break
    if idx is None:
        return

    wm_x, wm_y = canvas.coords(wm_item)
    st["drag_dx"] = event.x - wm_x
    st["drag_dy"] = event.y - wm_y
    st["dragging"] = True
    st["active_index"] = idx


def _on_wm_drag_motion(event):
    st = _manual_preview_state
    if not st.get("dragging"):
        return

    canvas = watermark_preview_canvas
    idx = st.get("active_index")
    if idx is None:
        return
    items = st.get("wm_items") or []
    if idx < 0 or idx >= len(items):
        return
    wm_item = items[idx]

    xmin, xmax, ymin, ymax = st["bbox"]
    wm_w = st["wm_w"]
    wm_h = st["wm_h"]
    subject_mask = st["subject_mask"]

    disp_scale_x = st["disp_scale_x"]
    disp_scale_y = st["disp_scale_y"]
    min_subject_coverage = st["min_subject_coverage"]

    # Candidate canvas coords -> target coords.
    canvas_x = event.x - st["drag_dx"]
    canvas_y = event.y - st["drag_dy"]

    x_try = int(round(canvas_x / disp_scale_x))
    y_try = int(round(canvas_y / disp_scale_y))

    x_try = max(xmin, min(x_try, xmax - wm_w + 1))
    y_try = max(ymin, min(y_try, ymax - wm_h + 1))

    region = subject_mask[y_try:y_try + wm_h, x_try:x_try + wm_w]
    if region.size == 0 or float(region.mean()) < min_subject_coverage:
        return  # reject invalid placements during drag

    # Update visuals + stored relative position.
    new_canvas_x = x_try * disp_scale_x
    new_canvas_y = y_try * disp_scale_y
    canvas.coords(wm_item, new_canvas_x, new_canvas_y)

    global manual_positions_by_path
    file_path = st.get("file_path")
    if not file_path:
        return
    positions = manual_positions_by_path.get(file_path, [])
    if idx >= len(positions):
        while len(positions) <= idx:
            positions.append((0.5, 0.5))
    positions[idx] = (x_try / float(st["target_w"]), y_try / float(st["target_h"]))
    manual_positions_by_path[file_path] = positions


def _on_wm_drag_release(_event):
    st = _manual_preview_state
    if st:
        st["dragging"] = False
        st["active_index"] = None


def on_manual_placement_toggle():
    """
    When manual placement is enabled, the Count slider controls how many manual
    watermarks you can drag in the preview.
    """
    global manual_positions_by_path

    if manual_watermark_enabled is None:
        return

    if manual_watermark_enabled.get():
        # Ensure the current preview image has an initialized positions list.
        if not selected_files:
            update_watermark_preview()
            return
        idx = 0
        try:
            if preview_image_index is not None:
                idx = int(preview_image_index.get())
        except Exception:
            idx = 0
        idx = max(0, min(idx, len(selected_files) - 1))
        path = selected_files[idx]

        positions = manual_positions_by_path.get(path, [])
        if not positions:
            positions = [(0.5, 0.5)]

        if count_slider is not None:
            try:
                desired = int(count_slider.get())
            except Exception:
                desired = 1
            desired = max(1, min(desired, 10))
            if len(positions) < desired:
                positions = positions + [(0.5, 0.5)] * (desired - len(positions))
            elif len(positions) > desired:
                positions = positions[:desired]

        manual_positions_by_path[path] = positions
    else:
        # Leaving manual mode keeps the slider count behavior as-is.
        pass

    update_watermark_preview()


def clear_results():
    result_before_value.config(text="-")
    result_after_value.config(text="-")
    result_saved_value.config(text="-")
    result_count_value.config(text="-")
    progress_bar["value"] = 0
    progress_bar["maximum"] = 100
    progress_text_label.config(text="0 / 0 completed")
    set_status_chip("ready")


def set_status_chip(state):
    global current_status_state
    current_status_state = state

    if state == "processing":
        text = "Processing"
        bg = theme["CHIP_PROCESSING_BG"]
        fg = theme["CHIP_PROCESSING_FG"]
    elif state == "done":
        text = "Done"
        bg = theme["CHIP_DONE_BG"]
        fg = theme["CHIP_DONE_FG"]
    else:
        text = "Ready"
        bg = theme["CHIP_READY_BG"]
        fg = theme["CHIP_READY_FG"]

    status_label.config(text=text, bg=bg, fg=fg)


# -----------------------------
# File actions
# -----------------------------
def select_images():
    global selected_files

    files = filedialog.askopenfilenames(
        title="Select image files",
        filetypes=[
            ("Image Files", "*.png *.jpg *.jpeg *.webp *.bmp *.tiff"),
            ("All Files", "*.*")
        ]
    )

    if files:
        selected_files = list(files)
    else:
        selected_files = []

    update_selected_label()
    refresh_file_table()
    update_preview()
    update_watermark_preview()
    clear_results()


def on_drop(event):
    global selected_files
    dropped_files = parse_dropped_files(event.data)

    if not dropped_files:
        messagebox.showwarning("Invalid Files", "No supported image files were dropped.")
        return

    selected_files = dropped_files
    update_selected_label()
    refresh_file_table()
    update_preview()
    update_watermark_preview()
    clear_results()


def clear_files():
    global selected_files
    global manual_positions_by_path
    selected_files = []
    manual_positions_by_path = {}
    update_selected_label()
    refresh_file_table()
    update_preview()
    update_watermark_preview()
    clear_results()


# -----------------------------
# Main processing
# -----------------------------
def optimize_images():
    threading.Thread(target=_optimize_images_thread, daemon=True).start()


def _optimize_images_thread():
    if not selected_files:
        messagebox.showwarning("No Images", "Please select images.")
        return

    set_status_chip("processing")

    try:
        width = int(width_entry.get())
        height = int(height_entry.get())
    except:
        messagebox.showerror("Error", "Invalid size.")
        set_status_chip("ready")
        return

    base_name = sanitize_filename(rename_entry.get())
    start_number = int(start_number_entry.get())
    output_format = format_var.get().upper()

    output_folder = filedialog.askdirectory()
    if not output_folder:
        set_status_chip("ready")
        return

    total_files = len(selected_files)

    progress_bar["maximum"] = total_files
    progress_bar["value"] = 0

    total_before = 0
    total_after = 0
    success_count = 0

    for i, file_path in enumerate(selected_files, start=start_number):
        try:
            original_size = os.path.getsize(file_path)
            total_before += original_size

            with Image.open(file_path) as img:
                img.thumbnail((width, height), Image.LANCZOS)

                # APPLY WATERMARK
                if watermark_enabled.get():
                    wm_size_fraction = watermark_size_slider.get() if watermark_size_slider is not None else 0.12
                    manual_on = manual_watermark_enabled is not None and manual_watermark_enabled.get()
                    wm_count = count_slider.get()
                    wm_manual_positions = manual_positions_by_path.get(file_path) if manual_on else None
                    img = apply_smart_watermark(
                        img,
                        watermark_path,
                        wm_count,
                        opacity_slider.get(),
                        size_fraction=wm_size_fraction,
                        manual_positions_rel=wm_manual_positions
                    )

                name = f"{base_name}-{i}"

                if output_format == "JPG":
                    img = img.convert("RGB")
                    out = os.path.join(output_folder, name + ".jpg")
                    img.save(out, "JPEG", quality=95)

                elif output_format == "PNG":
                    out = os.path.join(output_folder, name + ".png")
                    img.save(out, "PNG")

                elif output_format == "WEBP":
                    out = os.path.join(output_folder, name + ".webp")
                    img.save(out, "WEBP", quality=90)

                total_after += os.path.getsize(out)
                success_count += 1

        except Exception as e:
            print(e)

        progress_bar["value"] = success_count
        progress_text_label.config(text=f"{success_count}/{total_files}")
        root.update_idletasks()

    result_before_value.config(text=format_bytes(total_before))
    result_after_value.config(text=format_bytes(total_after))
    result_saved_value.config(text=format_bytes(total_before - total_after))
    result_count_value.config(text=str(success_count))
    set_status_chip("done")

    messagebox.showinfo("Done", f"{success_count} images optimized!")


# -----------------------------
# App window
# -----------------------------
root = TkinterDnD.Tk()

# Now it's safe to create the BooleanVar variable
watermark_enabled = tk.BooleanVar(value=False)

try:
    icon = tk.PhotoImage(file=resource_path("icon.png"))
    root.iconphoto(True, icon)
except Exception:
    pass

root.title("Part Hive Image Optimizer")
root.geometry("1320x860")
root.minsize(1240, 780)
root.configure(bg=LIGHT_THEME["BG"])

style = ttk.Style()
try:
    style.theme_use("clam")
except tk.TclError:
    pass


# -----------------------------
# Main layout
# -----------------------------
main_container = tk.Frame(root, bg=LIGHT_THEME["BG"], padx=28, pady=24)
main_container.pack(fill="both", expand=True)

header_frame = tk.Frame(main_container, bg=LIGHT_THEME["BG"])
header_frame.pack(fill="x", pady=(0, 18))

header_left = tk.Frame(header_frame, bg=LIGHT_THEME["BG"])
header_left.pack(side="left", fill="x", expand=True)

app_title = tk.Label(
    header_left,
    text="Image Optimizer",
    font=("Segoe UI", 24, "bold"),
    bg=LIGHT_THEME["BG"],
    fg=LIGHT_THEME["TEXT"]
)
app_title.pack(anchor="w")

app_subtitle = tk.Label(
    header_left,
    text="Premium workflow for resizing, renaming, converting, and watermarking product images.",
    font=("Segoe UI", 11),
    bg=LIGHT_THEME["BG"],
    fg=LIGHT_THEME["MUTED"]
)
app_subtitle.pack(anchor="w", pady=(4, 0))

pill_badge = tk.Label(
    header_left,
    text="STUDIO",
    font=("Segoe UI", 8, "bold"),
    padx=10,
    pady=4,
    bg=LIGHT_THEME["CARD_2"],
    fg=LIGHT_THEME["ACCENT"],
)
pill_badge.pack(anchor="w", pady=(10, 0))

theme_toggle_wrap = tk.Frame(header_frame, bg=LIGHT_THEME["BG"])
theme_toggle_wrap.pack(side="right", anchor="ne")

theme_label = tk.Label(
    theme_toggle_wrap,
    text="Dark mode",
    font=("Segoe UI", 10),
    bg=LIGHT_THEME["BG"],
    fg=LIGHT_THEME["MUTED"]
)
theme_label.pack(side="left", padx=(0, 8))

theme_var = tk.BooleanVar(value=False)

theme_toggle = tk.Checkbutton(
    theme_toggle_wrap,
    variable=theme_var,
    command=apply_theme,
    bg=LIGHT_THEME["BG"],
    activebackground=LIGHT_THEME["BG"],
    selectcolor=LIGHT_THEME["BG"],
    fg=LIGHT_THEME["TEXT"]
)
theme_toggle.pack(side="left")

content_frame = tk.Frame(main_container, bg=LIGHT_THEME["BG"])
content_frame.pack(fill="both", expand=True)
# Draggable split layout for a professional, responsive workspace.
split_pane = ttk.Panedwindow(content_frame, orient="horizontal")
split_pane.pack(fill="both", expand=True)

left_panel = tk.Frame(
    split_pane,
    bg=LIGHT_THEME["CARD"],
    bd=0,
    highlightthickness=1,
    highlightbackground=LIGHT_THEME["BORDER"]
)
right_panel = tk.Frame(
    split_pane,
    bg=LIGHT_THEME["CARD"],
    bd=0,
    highlightthickness=1,
    highlightbackground=LIGHT_THEME["BORDER"]
)

split_pane.add(left_panel, weight=5)
split_pane.add(right_panel, weight=7)


def _set_initial_split():
    total_width = max(1100, content_frame.winfo_width())
    left_width = int(total_width * 0.40)
    left_width = max(500, min(left_width, total_width - 620))
    try:
        split_pane.sashpos(0, left_width)
    except Exception:
        pass

# -----------------------------
# Left panel
# -----------------------------
root.after(120, _set_initial_split)

# Make the left panel scrollable so all settings (including Watermark)
# remain reachable on smaller displays.
left_canvas = tk.Canvas(left_panel, bg=LIGHT_THEME["CARD"], highlightthickness=0)
left_scrollbar = tk.Scrollbar(left_panel, orient="vertical", command=left_canvas.yview)
left_canvas.configure(yscrollcommand=left_scrollbar.set)

left_inner = tk.Frame(left_canvas, bg=LIGHT_THEME["CARD"], padx=22, pady=22)
left_window_id = left_canvas.create_window((0, 0), window=left_inner, anchor="nw")

left_canvas.pack(side="left", fill="both", expand=True)
left_scrollbar.pack(side="right", fill="y")


def _update_left_scrollregion(_event=None):
    # Ensure the scrollbar covers the entire content height.
    left_canvas.configure(scrollregion=left_canvas.bbox("all"))


def _on_left_canvas_configure(event):
    # Keep the inner frame aligned to the current canvas width.
    left_canvas.itemconfig(left_window_id, width=event.width)


left_inner.bind("<Configure>", _update_left_scrollregion)
left_canvas.bind("<Configure>", _on_left_canvas_configure)

# Add the widgets in left_inner as before (keep the original widget structure)
left_title = tk.Label(
    left_inner,
    text="◆ Upload & Settings",
    font=("Segoe UI", 14, "bold"),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["TEXT"]
)
left_title.pack(anchor="w")

left_desc = tk.Label(
    left_inner,
    text="Add your files, set the dimensions, rename pattern, and format.",
    font=("Segoe UI", 10),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["MUTED"]
)
left_desc.pack(anchor="w", pady=(6, 18))

button_row = tk.Frame(left_inner, bg=LIGHT_THEME["CARD"])
button_row.pack(fill="x", pady=(0, 14))

select_button = CustomButton(
    button_row,
    text="Select Images",
    command=select_images,
    variant="secondary",
    parent_bg=LIGHT_THEME["CARD"]
)
select_button.pack(side="left", padx=(0, 10))

clear_button = CustomButton(
    button_row,
    text="Clear Files",
    command=clear_files,
    variant="secondary",
    parent_bg=LIGHT_THEME["CARD"]
)
clear_button.pack(side="left")

drop_frame = tk.Frame(left_inner, height=128, highlightthickness=1)
drop_frame.pack(fill="x", pady=(0, 14))
drop_frame.pack_propagate(False)

drop_title = tk.Label(
    drop_frame,
    text="Drag and drop image files here",
    font=("Segoe UI", 12, "bold")
)
drop_title.place(relx=0.5, rely=0.42, anchor="center")

drop_subtitle = tk.Label(
    drop_frame,
    text="Supports PNG, JPG, JPEG, WEBP, BMP, and TIFF",
    font=("Segoe UI", 9)
)
drop_subtitle.place(relx=0.5, rely=0.62, anchor="center")

drop_frame.drop_target_register(DND_FILES)
drop_frame.dnd_bind("<<Drop>>", on_drop)

selected_count_label = tk.Label(
    left_inner,
    text="No images selected",
    font=("Segoe UI", 10, "bold"),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["TEXT"]
)
selected_count_label.pack(anchor="w", pady=(4, 16))

settings_card = tk.Frame(left_inner, highlightthickness=1, padx=14, pady=14)
settings_card.pack(fill="x")

settings_card.grid_columnconfigure(1, weight=1)
settings_card.grid_columnconfigure(3, weight=1)

settings_labels = []

lbl = tk.Label(settings_card, text="Width (px)", font=("Segoe UI", 10))
lbl.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=8)
settings_labels.append(lbl)

width_entry = tk.Entry(settings_card, font=("Segoe UI", 10))
width_entry.grid(row=0, column=1, sticky="ew", pady=8)
width_entry.insert(0, "800")

lbl = tk.Label(settings_card, text="Height (px)", font=("Segoe UI", 10))
lbl.grid(row=0, column=2, sticky="w", padx=(18, 10), pady=8)
settings_labels.append(lbl)

height_entry = tk.Entry(settings_card, font=("Segoe UI", 10))
height_entry.grid(row=0, column=3, sticky="ew", pady=8)
height_entry.insert(0, "600")

lbl = tk.Label(settings_card, text="Base File Name", font=("Segoe UI", 10))
lbl.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=8)
settings_labels.append(lbl)

rename_entry = tk.Entry(settings_card, font=("Segoe UI", 10))
rename_entry.grid(row=1, column=1, sticky="ew", pady=8)
rename_entry.insert(0, "Test")

lbl = tk.Label(settings_card, text="Starting Number", font=("Segoe UI", 10))
lbl.grid(row=1, column=2, sticky="w", padx=(18, 10), pady=8)
settings_labels.append(lbl)

start_number_entry = tk.Entry(settings_card, font=("Segoe UI", 10))
start_number_entry.grid(row=1, column=3, sticky="ew", pady=8)
start_number_entry.insert(0, "1")

lbl = tk.Label(settings_card, text="Output Format", font=("Segoe UI", 10))
lbl.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
settings_labels.append(lbl)

format_var = tk.StringVar(value="WEBP")
format_menu = ttk.Combobox(
    settings_card,
    textvariable=format_var,
    values=["JPG", "PNG", "WEBP"],
    state="readonly",
    style="Modern.TCombobox",
    font=("Segoe UI", 10)
)
format_menu.grid(row=2, column=1, sticky="ew", pady=(8, 0))
format_menu.current(2)

files_section = tk.Frame(left_inner, bg=LIGHT_THEME["CARD"])
files_section.pack(fill="both", expand=True, pady=(16, 0))

files_title = tk.Label(
    files_section,
    text="◈ Loaded Files",
    font=("Segoe UI", 12, "bold"),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["TEXT"]
)
files_title.pack(anchor="w", pady=(0, 8))

file_list_frame = tk.Frame(files_section, bg=LIGHT_THEME["CARD"])
file_list_frame.pack(fill="both", expand=True)

file_scrollbar = tk.Scrollbar(file_list_frame)
file_scrollbar.pack(side="right", fill="y")

file_listbox = tk.Listbox(
    file_list_frame,
    height=14,
    yscrollcommand=file_scrollbar.set,
    font=("Consolas", 10)
)
file_listbox.pack(fill="both", expand=True)
file_scrollbar.config(command=file_listbox.yview)

# -----------------------------
# Watermark Section
# -----------------------------
watermark_frame = tk.Frame(left_inner, highlightthickness=1, padx=14, pady=14)
watermark_frame.pack(fill="x", pady=(14, 0))

tk.Label(watermark_frame, text="◉ Watermark Studio", font=("Segoe UI", 12, "bold")).pack(anchor="w")

toggle = tk.Checkbutton(
    watermark_frame,
    text="Enable watermark",
    variable=watermark_enabled,
    command=update_watermark_preview
)
toggle.pack(anchor="w", pady=(6, 6))

manual_watermark_enabled = tk.BooleanVar(value=False)
manual_toggle = tk.Checkbutton(
    watermark_frame,
    text="Manual placement (drag in preview)",
    variable=manual_watermark_enabled,
    command=on_manual_placement_toggle
)
manual_toggle.pack(anchor="w", pady=(0, 10))

def select_watermark():
    global watermark_path
    file = filedialog.askopenfilename(
        filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")]
    )
    if file:
        watermark_path = file
        watermark_label.config(text=os.path.basename(file))
        update_watermark_preview()

tk.Button(watermark_frame, text="Upload Watermark", command=select_watermark).pack(anchor="w")

watermark_label = tk.Label(watermark_frame, text="No watermark selected")
watermark_label.pack(anchor="w", pady=(4, 10))

# OPACITY
tk.Label(watermark_frame, text="Opacity").pack(anchor="w")
opacity_slider = tk.Scale(
    watermark_frame,
    from_=0.1,
    to=1.0,
    resolution=0.1,
    orient="horizontal",
    command=update_watermark_preview,
)
opacity_slider.set(0.3)
opacity_slider.pack(fill="x")

# SIZE
tk.Label(watermark_frame, text="Watermark size (% of image width)").pack(anchor="w")
watermark_size_slider = tk.Scale(
    watermark_frame,
    from_=0.05,
    to=0.25,
    resolution=0.01,
    orient="horizontal",
    command=update_watermark_preview,
)
watermark_size_slider.set(0.12)
watermark_size_slider.pack(fill="x")

# COUNT
tk.Label(watermark_frame, text="Count").pack(anchor="w")
count_slider = tk.Scale(
    watermark_frame, from_=1, to=10, orient="horizontal", command=update_watermark_preview
)
count_slider.set(3)
count_slider.pack(fill="x")

# -----------------------------
# Right panel
# -----------------------------
# Make the right panel scrollable so actions (Optimize button)
# remain reachable on smaller displays.
right_canvas = tk.Canvas(right_panel, bg=LIGHT_THEME["CARD"], highlightthickness=0)
right_scrollbar = tk.Scrollbar(right_panel, orient="vertical", command=right_canvas.yview)
right_canvas.configure(yscrollcommand=right_scrollbar.set)

right_inner = tk.Frame(right_canvas, bg=LIGHT_THEME["CARD"], padx=22, pady=22)
right_window_id = right_canvas.create_window((0, 0), window=right_inner, anchor="nw")

right_canvas.pack(side="left", fill="both", expand=True)
right_scrollbar.pack(side="right", fill="y")


def _update_right_scrollregion(_event=None):
    right_canvas.configure(scrollregion=right_canvas.bbox("all"))


def _on_right_canvas_configure(event):
    right_canvas.itemconfig(right_window_id, width=event.width)


right_inner.bind("<Configure>", _update_right_scrollregion)
right_canvas.bind("<Configure>", _on_right_canvas_configure)

right_title = tk.Label(
    right_inner,
    text="◆ Preview & Results",
    font=("Segoe UI", 16, "bold"),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["TEXT"]
)
right_title.pack(anchor="w")

right_desc = tk.Label(
    right_inner,
    text="Fine-tune watermark placement and monitor optimization output in real time.",
    font=("Segoe UI", 10),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["MUTED"]
)
right_desc.pack(anchor="w", pady=(4, 16))

watermark_preview_card = tk.Frame(
    right_inner,
    bg=LIGHT_THEME["CARD_2"],
    highlightthickness=1,
    highlightbackground=LIGHT_THEME["BORDER"],
    padx=14,
    pady=14,
)
watermark_preview_card.pack(fill="x", pady=(0, 14))

watermark_preview_title = tk.Label(
    watermark_preview_card,
    text="◉ Live Watermark Preview",
    font=("Segoe UI", 12, "bold"),
    bg=LIGHT_THEME["CARD_2"],
    fg=LIGHT_THEME["TEXT"],
)
watermark_preview_title.pack(anchor="w", pady=(0, 8))

preview_image_index = tk.IntVar(value=0)

preview_picker_row = tk.Frame(watermark_preview_card, bg=LIGHT_THEME["CARD_2"])
preview_picker_row.pack(fill="x", pady=(0, 10))

preview_picker_label = tk.Label(
    preview_picker_row,
    text="Preview image",
    font=("Segoe UI", 10),
    bg=LIGHT_THEME["CARD_2"],
    fg=LIGHT_THEME["MUTED"],
)
preview_picker_label.pack(side="left")

preview_prev_btn = CustomButton(
    preview_picker_row,
    text="‹",
    command=preview_prev_image,
    variant="secondary",
    parent_bg=LIGHT_THEME["CARD"],
)
preview_prev_btn.pack(side="left", padx=(10, 6))

preview_image_picker = ttk.Combobox(
    preview_picker_row,
    values=[],
    state="readonly",
    style="Modern.TCombobox",
    font=("Segoe UI", 10),
    width=28,
)
preview_image_picker.pack(side="left", fill="x", expand=True)
preview_image_picker.bind("<<ComboboxSelected>>", on_preview_image_selected)

preview_next_btn = CustomButton(
    preview_picker_row,
    text="›",
    command=preview_next_image,
    variant="secondary",
    parent_bg=LIGHT_THEME["CARD"],
)
preview_next_btn.pack(side="left", padx=(6, 0))

preview_image_label = tk.Label(
    watermark_preview_card,
    text="-",
    font=("Segoe UI", 9),
    bg=LIGHT_THEME["CARD_2"],
    fg=LIGHT_THEME["MUTED"],
)
preview_image_label.pack(anchor="w", pady=(0, 8))

watermark_preview_canvas = tk.Canvas(
    watermark_preview_card,
    bg=LIGHT_THEME["INPUT_BG"],
    highlightthickness=1,
    highlightbackground=LIGHT_THEME["BORDER"],
    width=_PREVIEW_MAX_W,
    height=_PREVIEW_MAX_H,
)
watermark_preview_canvas.pack(fill="x", pady=(0, 10))

watermark_preview_hint = tk.Label(
    watermark_preview_card,
    text="Use Manual placement to drag the watermark inside the subject.",
    font=("Segoe UI", 9),
    bg=LIGHT_THEME["CARD_2"],
    fg=LIGHT_THEME["MUTED"],
)
watermark_preview_hint.pack(anchor="w", pady=(0, 14))

preview_section = tk.Frame(
    right_inner,
    bg=LIGHT_THEME["CARD_2"],
    highlightthickness=1,
    highlightbackground=LIGHT_THEME["BORDER"],
    padx=14,
    pady=14,
)
preview_section.pack(fill="both", expand=True, pady=(0, 14))

preview_title = tk.Label(
    preview_section,
    text="◈ Final File Name Preview",
    font=("Segoe UI", 12, "bold"),
    bg=LIGHT_THEME["CARD_2"],
    fg=LIGHT_THEME["TEXT"]
)
preview_title.pack(anchor="w", pady=(0, 8))

preview_frame = tk.Frame(preview_section, bg=LIGHT_THEME["CARD_2"])
preview_frame.pack(fill="both", expand=True, pady=(0, 16))

preview_scrollbar = tk.Scrollbar(preview_frame)
preview_scrollbar.pack(side="right", fill="y")

preview_listbox = tk.Listbox(
    preview_frame,
    height=12,
    yscrollcommand=preview_scrollbar.set,
    font=("Consolas", 10)
)
preview_listbox.pack(fill="both", expand=True)
preview_scrollbar.config(command=preview_listbox.yview)

results_box = tk.Frame(right_inner, highlightthickness=1, padx=14, pady=14)
results_box.pack(fill="x", pady=(0, 16))

result_key_labels = []
result_value_labels = []

lbl = tk.Label(results_box, text="Total Before", font=("Segoe UI", 10))
lbl.grid(row=0, column=0, sticky="w", pady=6)
result_key_labels.append(lbl)

result_before_value = tk.Label(results_box, text="-", font=("Segoe UI", 10, "bold"))
result_before_value.grid(row=0, column=1, sticky="w", padx=(14, 0), pady=6)
result_value_labels.append(result_before_value)

lbl = tk.Label(results_box, text="Total After", font=("Segoe UI", 10))
lbl.grid(row=1, column=0, sticky="w", pady=6)
result_key_labels.append(lbl)

result_after_value = tk.Label(results_box, text="-", font=("Segoe UI", 10, "bold"))
result_after_value.grid(row=1, column=1, sticky="w", padx=(14, 0), pady=6)
result_value_labels.append(result_after_value)

lbl = tk.Label(results_box, text="Saved", font=("Segoe UI", 10))
lbl.grid(row=2, column=0, sticky="w", pady=6)
result_key_labels.append(lbl)

result_saved_value = tk.Label(results_box, text="-", font=("Segoe UI", 10, "bold"))
result_saved_value.grid(row=2, column=1, sticky="w", padx=(14, 0), pady=6)
result_value_labels.append(result_saved_value)

lbl = tk.Label(results_box, text="Processed", font=("Segoe UI", 10))
lbl.grid(row=3, column=0, sticky="w", pady=6)
result_key_labels.append(lbl)

result_count_value = tk.Label(results_box, text="-", font=("Segoe UI", 10, "bold"))
result_count_value.grid(row=3, column=1, sticky="w", padx=(14, 0), pady=6)
result_value_labels.append(result_count_value)

action_row = tk.Frame(right_inner, bg=LIGHT_THEME["CARD"])
action_row.pack(fill="x")

optimize_button = CustomButton(
    action_row,
    text="Optimize",
    command=optimize_images,
    variant="primary",
    fill_x=True,
    parent_bg=LIGHT_THEME["CARD"]
)
optimize_button.pack(fill="x")

progress_wrap = tk.Frame(right_inner, bg=LIGHT_THEME["CARD"])
progress_wrap.pack(fill="x", pady=(16, 0))

progress_bar = ttk.Progressbar(
    progress_wrap,
    orient="horizontal",
    mode="determinate",
    style="Modern.Horizontal.TProgressbar"
)
progress_bar.pack(fill="x")

progress_text_label = tk.Label(
    right_inner,
    text="0 / 0 completed",
    font=("Segoe UI", 10),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["MUTED"]
)
progress_text_label.pack(anchor="w", pady=(8, 0))

status_label = tk.Label(
    right_inner,
    text="Ready",
    font=("Segoe UI", 9, "bold"),
    padx=12,
    pady=5,
    bd=0,
    bg=LIGHT_THEME["CHIP_READY_BG"],
    fg=LIGHT_THEME["CHIP_READY_FG"]
)
status_label.pack(anchor="w", pady=(6, 0))


# -----------------------------
# Bindings
# -----------------------------
rename_entry.bind("<KeyRelease>", update_preview)
start_number_entry.bind("<KeyRelease>", update_preview)
width_entry.bind("<KeyRelease>", update_watermark_preview)
height_entry.bind("<KeyRelease>", update_watermark_preview)
format_var.trace_add("write", update_preview)

# -----------------------------
# Start
# -----------------------------
apply_theme()
update_selected_label()
refresh_file_table()
update_preview()
update_watermark_preview()
refresh_preview_image_picker()
clear_results()

root.mainloop()