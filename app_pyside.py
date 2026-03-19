import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QSizePolicy,
    QWidget,
)

Image.MAX_IMAGE_PIXELS = None
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}


def _load_app_icon() -> QIcon | None:
    """
    Best-effort icon loader for dev runs and packaged builds.
    Note: OS-level process/dock icons can still show Python when running
    from `python ...` (that is controlled by packaging/launcher), but this will
    correctly set the window/taskbar icon.
    """
    base_dir = Path(__file__).resolve().parent
    for name in ("icon.icns", "icon.ico", "icon.png"):
        p = base_dir / name
        if p.exists():
            icon = QIcon(str(p))
            if not icon.isNull():
                return icon
    return None


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", name.strip())


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def apply_smart_watermark(
    img: Image.Image,
    watermark_file: str | None,
    count: int,
    opacity: float,
    size_fraction: float = 0.12,
    manual_positions_rel: list[tuple[float, float]] | None = None,
    rng: random.Random | None = None,
) -> Image.Image:
    if not watermark_file:
        return img
    rng = rng or random.Random()
    try:
        watermark = Image.open(watermark_file).convert("RGBA")
    except Exception:
        return img

    base = img.convert("RGBA")
    w, h = base.size
    ww, wh = watermark.size
    if w <= 1 or h <= 1 or ww <= 0 or wh <= 0:
        return base

    rgb = np.array(base.convert("RGB"), dtype=np.float32)
    corner = max(10, int(min(w, h) * 0.06))
    tl = rgb[0:corner, 0:corner, :]
    tr = rgb[0:corner, w - corner:w, :]
    bl = rgb[h - corner:h, 0:corner, :]
    br = rgb[h - corner:h, w - corner:w, :]
    bg_color = np.median(np.concatenate([tl.reshape(-1, 3), tr.reshape(-1, 3), bl.reshape(-1, 3), br.reshape(-1, 3)]), axis=0)
    dist = np.sqrt(((rgb - bg_color) ** 2).sum(axis=2))
    flat = dist.reshape(-1)
    subject_mask = None
    for perc in (70, 75, 80, 82, 84, 86, 88, 90, 92, 94):
        t = np.percentile(flat, perc)
        m = dist > t
        if 0.05 <= float(m.mean()) <= 0.6:
            subject_mask = m
            break
    if subject_mask is None:
        subject_mask = dist > np.percentile(flat, 85)

    ys, xs = np.where(subject_mask)
    margin = max(10, min(int(min(w, h) * 0.05), 80))
    if xs.size > 0:
        xmin, xmax = int(xs.min()), int(xs.max())
        ymin, ymax = int(ys.min()), int(ys.max())
        pad = int(min(w, h) * 0.02)
        xmin = max(0, xmin - pad)
        ymin = max(0, ymin - pad)
        xmax = min(w - 1, xmax + pad)
        ymax = min(h - 1, ymax + pad)
    else:
        xmin, xmax, ymin, ymax = margin, w - margin - 1, margin, h - margin - 1

    min_w_frac, max_w_frac = 0.03, 0.28
    target_w_frac = max(min_w_frac, min(size_fraction, max_w_frac))
    wm_w = int(max(1, w * target_w_frac))
    aspect = wh / float(ww)
    wm_h = int(max(1, wm_w * aspect))
    wm_h = min(wm_h, max(1, int(h * 0.28)))
    wm_w = max(1, min(wm_w, w - 1))
    wm_h = max(1, min(wm_h, h - 1))
    wm = watermark.resize((wm_w, wm_h))
    alpha = wm.split()[3].point(lambda p: int(p * float(opacity)))
    wm.putalpha(alpha)

    def subject_ok(x: int, y: int) -> bool:
        region = subject_mask[y : y + wm_h, x : x + wm_w]
        return region.size > 0 and float(region.mean()) >= 0.6

    result = base.copy()
    if manual_positions_rel:
        for xr, yr in manual_positions_rel:
            x = int(max(0.0, min(1.0, xr)) * w)
            y = int(max(0.0, min(1.0, yr)) * h)
            x = max(xmin, min(x, xmax - wm_w + 1))
            y = max(ymin, min(y, ymax - wm_h + 1))
            if subject_ok(x, y):
                result.paste(wm, (x, y), wm)
        return result

    count = max(1, min(int(count), 25))
    placed: list[tuple[int, int, int, int]] = []
    gap = max(8, min(int(min(w, h) * 0.04), 60))
    for _ in range(count):
        placed_this = False
        for _ in range(220):
            x = rng.randint(xmin, max(xmin, xmax - wm_w + 1))
            y = rng.randint(ymin, max(ymin, ymax - wm_h + 1))
            if not subject_ok(x, y):
                continue
            overlap = False
            for px, py, pw, ph in placed:
                if not (x + wm_w + gap < px or x > px + pw + gap or y + wm_h + gap < py or y > py + ph + gap):
                    overlap = True
                    break
            if overlap:
                continue
            result.paste(wm, (x, y), wm)
            placed.append((x, y, wm_w, wm_h))
            placed_this = True
            break
        if not placed_this:
            break
    return result


@dataclass
class PreviewState:
    image_path: str | None = None
    image_size: tuple[int, int] = (1, 1)
    display_size: tuple[int, int] = (1, 1)
    wm_size: tuple[int, int] = (1, 1)


def estimate_subject_mask_and_bbox(base_rgba: Image.Image):
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
    dist = np.sqrt(((rgb - bg_color) ** 2).sum(axis=2))
    flat = dist.reshape(-1)
    subject_mask = None
    for perc in (70, 75, 80, 82, 84, 86, 88, 90, 92, 94):
        t = np.percentile(flat, perc)
        m = dist > t
        if 0.05 <= float(m.mean()) <= 0.6:
            subject_mask = m
            break
    if subject_mask is None:
        subject_mask = dist > np.percentile(flat, 85)

    ys, xs = np.where(subject_mask)
    margin = max(10, min(int(min(w, h) * 0.05), 80))
    if xs.size > 0:
        xmin, xmax = int(xs.min()), int(xs.max())
        ymin, ymax = int(ys.min()), int(ys.max())
        pad = int(min(w, h) * 0.02)
        xmin = max(0, xmin - pad)
        ymin = max(0, ymin - pad)
        xmax = min(w - 1, xmax + pad)
        ymax = min(h - 1, ymax + pad)
        if (xmax - xmin + 1) * (ymax - ymin + 1) < 0.10 * w * h:
            xmin, xmax, ymin, ymax = margin, w - margin - 1, margin, h - margin - 1
    else:
        xmin, xmax, ymin, ymax = margin, w - margin - 1, margin, h - margin - 1
    return subject_mask, xmin, xmax, ymin, ymax


class DropZone(QFrame):
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("DropZone")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(6)
        self.title = QLabel("Drag & drop images here")
        self.sub = QLabel("PNG, JPG, JPEG, WEBP, BMP, TIFF")
        self.title.setObjectName("DropTitle")
        self.sub.setObjectName("DropSub")
        self.title.setAlignment(Qt.AlignCenter)
        self.sub.setAlignment(Qt.AlignCenter)
        lay.addStretch(1)
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addStretch(1)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        files = []
        for u in urls:
            p = u.toLocalFile()
            if not p:
                continue
            if Path(p).suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(p)
        if files:
            self.filesDropped.emit(files)
        event.acceptProposedAction()


class PreviewWidget(QLabel):
    positionsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Preview card already draws borders; keep this label clean to avoid
        # a "double box" effect.
        self.setStyleSheet("QLabel { background: transparent; border: none; }")
        self.state = PreviewState()
        self.markers: list[tuple[float, float]] = []
        self.drag_index: int | None = None
        self._subject_mask = None
        self._bbox = (0, 0, 0, 0)
        # Slightly permissive during drag so users can always reposition.
        self._min_subject_coverage = 0.45
        self._watermark_pixmap: QPixmap | None = None

    def set_data(
        self,
        pixmap: QPixmap,
        image_path: str,
        image_size: tuple[int, int],
        wm_size: tuple[int, int],
        markers: list[tuple[float, float]],
        subject_mask=None,
        bbox=(0, 0, 0, 0),
        watermark_pixmap: QPixmap | None = None,
    ):
        self.setPixmap(pixmap)
        self.state = PreviewState(image_path=image_path, image_size=image_size, display_size=(pixmap.width(), pixmap.height()), wm_size=wm_size)
        self.markers = list(markers)
        self._subject_mask = subject_mask
        self._bbox = bbox
        self._watermark_pixmap = watermark_pixmap
        self.update()

    def _event_to_rel(self, pos: QPoint) -> tuple[float, float]:
        pm = self.pixmap()
        if not pm:
            return 0.5, 0.5
        left = (self.width() - pm.width()) // 2
        top = (self.height() - pm.height()) // 2
        x = max(left, min(pos.x(), left + pm.width() - 1)) - left
        y = max(top, min(pos.y(), top + pm.height() - 1)) - top
        return x / max(1, pm.width()), y / max(1, pm.height())

    def paintEvent(self, event):
        super().paintEvent(event)
        pm = self.pixmap()
        if not pm or not self.markers:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        left = (self.width() - pm.width()) // 2
        top = (self.height() - pm.height()) // 2
        pen = QPen(Qt.cyan, 2)
        painter.setPen(pen)

        if self._watermark_pixmap:
            overlay_w = self._watermark_pixmap.width()
            overlay_h = self._watermark_pixmap.height()
        else:
            overlay_w = max(8, int(self.state.wm_size[0] * (pm.width() / max(1, self.state.image_size[0]))))
            overlay_h = max(8, int(self.state.wm_size[1] * (pm.height() / max(1, self.state.image_size[1]))))
        for i, (xr, yr) in enumerate(self.markers):
            x = left + int(xr * pm.width())
            y = top + int(yr * pm.height())
            if self._watermark_pixmap:
                painter.drawPixmap(x, y, self._watermark_pixmap)
                # Outline to show draggable bounds.
                painter.drawRoundedRect(QRect(x, y, overlay_w, overlay_h), 6, 6)
                painter.drawText(x + 6, y + 16, str(i + 1))
            else:
                painter.drawRoundedRect(QRect(x, y, overlay_w, overlay_h), 6, 6)
                painter.drawText(x + 6, y + 16, str(i + 1))
        painter.end()

    def mousePressEvent(self, event):
        pm = self.pixmap()
        if not pm or not self.markers:
            return
        rx, ry = self._event_to_rel(event.position().toPoint())
        closest = None
        best = 10_000.0
        for i, (mx, my) in enumerate(self.markers):
            d = abs(mx - rx) + abs(my - ry)
            if d < best:
                closest, best = i, d
        self.drag_index = closest

    def mouseMoveEvent(self, event):
        if self.drag_index is None:
            return
        rx, ry = self._event_to_rel(event.position().toPoint())
        # Constrain drag to the subject bbox and require sufficient subject coverage.
        if self._subject_mask is not None:
            w, h = self.state.image_size
            wm_w, wm_h = self.state.wm_size
            xmin, xmax, ymin, ymax = self._bbox
            x = int(rx * w)
            y = int(ry * h)
            x = max(xmin, min(x, xmax - wm_w + 1))
            y = max(ymin, min(y, ymax - wm_h + 1))
            region = self._subject_mask[y : y + wm_h, x : x + wm_w]
            if region.size == 0:
                return
            # Require coverage only if we have enough subject pixels.
            if float(region.mean()) < self._min_subject_coverage:
                return
            rx = x / max(1, w)
            ry = y / max(1, h)

        self.markers[self.drag_index] = (rx, ry)
        self.positionsChanged.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag_index = None
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Part Hive Image Optimizer - PySide")
        icon = _load_app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(1360, 900)
        self.selected_files: list[str] = []
        self.watermark_path: str | None = None
        self.manual_positions_by_path: dict[str, list[tuple[float, float]]] = {}
        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(12)

        header_row = QHBoxLayout()
        header_left = QVBoxLayout()
        title = QLabel("Image Optimizer Studio")
        title.setObjectName("Title")
        subtitle = QLabel("Modern workflow for resize, convert, and advanced watermarking.")
        subtitle.setObjectName("Sub")
        header_left.addWidget(title)
        header_left.addWidget(subtitle)
        header_row.addLayout(header_left, 1)

        self.theme_toggle = QCheckBox("Dark mode")
        self.theme_toggle.setChecked(True)
        self.theme_toggle.toggled.connect(self._apply_styles)
        header_row.addWidget(self.theme_toggle, 0, Qt.AlignRight | Qt.AlignTop)

        outer.addLayout(header_row)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        # Left: scrollable settings + thumbnails
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setObjectName("ScrollCard")
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left = QFrame()
        left.setObjectName("Card")
        left_scroll.setWidget(left)

        # Right: main preview/results card
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setObjectName("ScrollCard")
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right = QFrame()
        right.setObjectName("Card")
        right_scroll.setWidget(right)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 8)
        left.setMinimumWidth(460)
        right.setMinimumWidth(0)
        splitter.setSizes([520, 980])

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, panel: QWidget):
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("◆ Upload & Settings"))

        # Drag-and-drop zone
        self.drop_zone = DropZone()
        self.drop_zone.filesDropped.connect(self._add_files)
        lay.addWidget(self.drop_zone)

        # Thumbnail grid (scrolls with the column)
        thumbs_card = QFrame()
        thumbs_card.setObjectName("ThumbsCard")
        thumbs_layout = QGridLayout(thumbs_card)
        thumbs_layout.setContentsMargins(6, 10, 6, 10)
        thumbs_layout.setHorizontalSpacing(8)
        thumbs_layout.setVerticalSpacing(8)
        self.thumbs_layout = thumbs_layout
        lay.addWidget(thumbs_card)

        btn_row = QHBoxLayout()
        self.select_btn = QPushButton("Select Images")
        self.clear_btn = QPushButton("Clear Files")
        self.select_btn.clicked.connect(self.select_images)
        self.clear_btn.clicked.connect(self.clear_files)
        btn_row.addWidget(self.select_btn)
        btn_row.addWidget(self.clear_btn)
        lay.addLayout(btn_row)

        form = QFormLayout()
        self.width_edit = QLineEdit("800")
        self.height_edit = QLineEdit("600")
        self.base_edit = QLineEdit("Test")
        self.start_edit = QLineEdit("1")
        self.format_combo = QComboBox()
        self.format_combo.addItems(["WEBP", "JPG", "PNG"])
        # Keep the dropdown within the scrollable left panel width.
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.format_combo.setMinimumWidth(180)
        self.format_combo.setMaximumWidth(220)
        self.format_combo.setMaxVisibleItems(3)
        # Keep "Final file name preview" in sync with inputs.
        self.base_edit.textChanged.connect(self.update_name_preview)
        self.start_edit.textChanged.connect(self.update_name_preview)
        self.format_combo.currentIndexChanged.connect(self.update_name_preview)
        # Put width + height side-by-side (premium, non-cramped layout).
        dim_container = QWidget()
        dim_layout = QHBoxLayout(dim_container)
        dim_layout.setContentsMargins(0, 0, 0, 0)
        dim_layout.setSpacing(12)

        wcol = QVBoxLayout()
        wcol.setContentsMargins(0, 0, 0, 0)
        wcol.setSpacing(4)
        wcol.addWidget(QLabel("Width (px)"))
        wcol.addWidget(self.width_edit)

        hcol = QVBoxLayout()
        hcol.setContentsMargins(0, 0, 0, 0)
        hcol.setSpacing(4)
        hcol.addWidget(QLabel("Height (px)"))
        hcol.addWidget(self.height_edit)

        dim_layout.addLayout(wcol, 1)
        dim_layout.addLayout(hcol, 1)
        form.addRow("Dimensions", dim_container)
        # Base Name + Starting Number side-by-side (premium input grouping).
        rename_container = QWidget()
        rename_layout = QHBoxLayout(rename_container)
        rename_layout.setContentsMargins(0, 0, 0, 0)
        rename_layout.setSpacing(12)

        bcol = QVBoxLayout()
        bcol.setContentsMargins(0, 0, 0, 0)
        bcol.setSpacing(4)
        bcol.addWidget(QLabel("Base Name"))
        bcol.addWidget(self.base_edit)

        scol = QVBoxLayout()
        scol.setContentsMargins(0, 0, 0, 0)
        scol.setSpacing(4)
        scol.addWidget(QLabel("Starting Number"))
        scol.addWidget(self.start_edit)

        rename_layout.addLayout(bcol, 1)
        rename_layout.addLayout(scol, 1)

        # Empty row label; the inner labels are part of the container.
        form.addRow("", rename_container)
        form.addRow("Output Format", self.format_combo)
        lay.addLayout(form)

        lay.addWidget(QLabel("◉ Watermark Studio"))
        self.enable_wm = QCheckBox("Enable watermark")
        self.manual_wm = QCheckBox("Manual placement")
        self.enable_wm.toggled.connect(self.update_preview)
        self.manual_wm.toggled.connect(self.update_preview)
        lay.addWidget(self.enable_wm)
        lay.addWidget(self.manual_wm)
        self.upload_wm_btn = QPushButton("Upload Watermark")
        self.upload_wm_btn.clicked.connect(self.select_watermark)
        self.wm_name = QLabel("No watermark selected")
        lay.addWidget(self.upload_wm_btn)
        lay.addWidget(self.wm_name)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setValue(30)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(5, 25)
        self.size_slider.setValue(12)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 10)
        self.count_spin.setValue(2)
        self.opacity_slider.valueChanged.connect(self.update_preview)
        self.size_slider.valueChanged.connect(self.update_preview)
        self.count_spin.valueChanged.connect(self.update_preview)
        form2 = QFormLayout()
        form2.addRow("Opacity", self.opacity_slider)
        form2.addRow("Size %", self.size_slider)
        form2.addRow("Count", self.count_spin)
        lay.addLayout(form2)

    def _build_right(self, panel: QWidget):
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(QLabel("◆ Preview & Results"))
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        self.prev_btn = QPushButton("‹")
        self.next_btn = QPushButton("›")
        self.preview_combo = QComboBox()
        self.prev_btn.clicked.connect(self.prev_preview_image)
        self.next_btn.clicked.connect(self.next_preview_image)
        self.preview_combo.currentIndexChanged.connect(self.update_preview)
        top.addWidget(QLabel("Preview image"))
        top.addWidget(self.prev_btn)
        # Keep responsive width while showing long names with eliding.
        self.preview_combo.setMinimumWidth(220)
        self.preview_combo.setMaxVisibleItems(12)
        self.preview_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preview_combo.setMinimumContentsLength(20)
        self.preview_combo.view().setTextElideMode(Qt.ElideMiddle)
        self.preview_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        top.addWidget(self.preview_combo, 1)
        top.addWidget(self.next_btn)
        lay.addLayout(top)

        preview_card = QFrame()
        preview_card.setObjectName("InnerCard")
        preview_card_lay = QVBoxLayout(preview_card)
        preview_card_lay.setContentsMargins(14, 14, 14, 14)
        preview_card_lay.setSpacing(8)

        self.preview = PreviewWidget()
        self.preview.positionsChanged.connect(self._save_preview_positions)
        preview_card_lay.addWidget(self.preview, 1)

        lay.addWidget(preview_card, 1)

        name_card = QFrame()
        name_card.setObjectName("InnerCard")
        name_card_lay = QVBoxLayout(name_card)
        name_card_lay.setContentsMargins(14, 14, 14, 14)
        name_card_lay.setSpacing(6)

        name_card_lay.addWidget(QLabel("◈ Final file name preview"))
        self.name_preview = QListWidget()
        self.name_preview.setMinimumHeight(110)
        name_card_lay.addWidget(self.name_preview)

        lay.addWidget(name_card, 0)

        stats = QGridLayout()
        self.before_lbl = QLabel("-")
        self.after_lbl = QLabel("-")
        self.saved_lbl = QLabel("-")
        self.count_lbl = QLabel("-")
        stats.addWidget(QLabel("Before"), 0, 0); stats.addWidget(self.before_lbl, 0, 1)
        stats.addWidget(QLabel("After"), 1, 0); stats.addWidget(self.after_lbl, 1, 1)
        stats.addWidget(QLabel("Saved"), 2, 0); stats.addWidget(self.saved_lbl, 2, 1)
        stats.addWidget(QLabel("Processed"), 3, 0); stats.addWidget(self.count_lbl, 3, 1)
        lay.addLayout(stats)

        self.optimize_btn = QPushButton("Optimize")
        self.optimize_btn.clicked.connect(self.optimize_images)
        self.progress = QProgressBar()
        self.status_chip = QLabel("Ready")
        self.status_chip.setObjectName("ChipReady")
        lay.addWidget(self.optimize_btn)
        lay.addWidget(self.progress)
        lay.addWidget(self.status_chip, 0, Qt.AlignLeft)

    def _apply_styles(self):
        dark = True
        try:
            dark = bool(self.theme_toggle.isChecked())
        except Exception:
            dark = True

        if dark:
            self.setStyleSheet(
                """
                QWidget { color:#e5e7eb; font-family: Arial, Helvetica, 'Segoe UI'; font-size:13px; }
                QMainWindow { background:#0b1020; }
                QFrame#Card { background:#111a2e; border:1px solid #2a3a59; border-radius:14px; }
                QScrollArea#ScrollCard { background: transparent; border: none; }
                QFrame#InnerCard { background:#0f172a; border:1px solid #2a3a59; border-radius:12px; }
                QFrame#ThumbsCard { background:#0f172a; border:1px solid #2a3a59; border-radius:12px; }
                QLabel#Title { font-size:28px; font-weight:700; color:#f8fafc; }
                QLabel#Sub { color:#9fb0cc; margin-bottom:4px; }
                QPushButton { background:#1a2742; border:1px solid #334b74; border-radius:10px; padding:9px 12px; }
                QPushButton:hover { background:#223357; }
                QPushButton:pressed { background:#263c67; }
                QLineEdit, QComboBox, QSpinBox, QListWidget {
                  background:#0f172a; border:1px solid #2a3a59; border-radius:10px; padding:7px;
                }
                QComboBox {
                  border-radius:10px;
                  padding-right: 28px;
                }
                QComboBox::drop-down {
                  subcontrol-origin: padding;
                  subcontrol-position: top right;
                  width: 28px;
                  border-left: 1px solid #2a3a59;
                  border-top-right-radius: 10px;
                  border-bottom-right-radius: 10px;
                  background: #0f172a;
                  border-top: 0px;
                  border-bottom: 0px;
                }
                QComboBox::down-arrow {
                  width: 10px;
                  height: 10px;
                  border-left: 5px solid transparent;
                  border-right: 5px solid transparent;
                  border-top: 6px solid #e5e7eb;
                  background: transparent;
                }
                QSpinBox {
                  border-radius:10px;
                  padding-right: 30px;
                }
                QSpinBox::up-button {
                  subcontrol-origin: border;
                  subcontrol-position: top right;
                  width: 22px;
                  border-left: 1px solid #2a3a59;
                  border-top-right-radius: 10px;
                  background: #0f172a;
                }
                QSpinBox::down-button {
                  subcontrol-origin: border;
                  subcontrol-position: bottom right;
                  width: 22px;
                  border-left: 1px solid #2a3a59;
                  border-bottom-right-radius: 10px;
                  background: #0f172a;
                }
                QSpinBox::up-arrow {
                  width: 0px;
                  height: 0px;
                  border-left: 4px solid transparent;
                  border-right: 4px solid transparent;
                  border-bottom: 6px solid #e5e7eb;
                }
                QSpinBox::down-arrow {
                  width: 0px;
                  height: 0px;
                  border-left: 4px solid transparent;
                  border-right: 4px solid transparent;
                  border-top: 6px solid #e5e7eb;
                }
                QProgressBar { border:1px solid #2a3a59; border-radius:10px; text-align:center; background:#0f172a; }
                QProgressBar::chunk { background:#6366f1; border-radius:10px; }
                QLabel#ChipReady { background:#1f2a45; color:#b8c7e6; border-radius:10px; padding:6px 12px; font-weight:600; }
                QFrame#DropZone { background:#0f172a; border:1px dashed #334155; border-radius:14px; }
                QLabel#DropTitle { color:#e5e7eb; font-size:14px; font-weight:700; }
                QLabel#DropSub { color:#9fb0cc; }
                """
            )
        else:
            self.setStyleSheet(
                """
                QWidget { color:#0b1220; font-family: Arial, Helvetica, 'Segoe UI'; font-size:13px; }
                QMainWindow { background:#eef2f8; }
                QFrame#Card { background:#ffffff; border:1px solid #d9e2f2; border-radius:14px; }
                QScrollArea#ScrollCard { background: transparent; border: none; }
                QFrame#InnerCard { background:#f6f8fd; border:1px solid #d9e2f2; border-radius:12px; }
                QFrame#ThumbsCard { background:#f5f8ff; border:1px solid #d9e2f2; border-radius:12px; }
                QLabel#Title { font-size:28px; font-weight:700; color:#0b1220; }
                QLabel#Sub { color:#5c6a84; margin-bottom:4px; }
                QPushButton { background:#f4f7fd; border:1px solid #d9e2f2; border-radius:10px; padding:9px 12px; }
                QPushButton:hover { background:#eaf0fb; }
                QPushButton:pressed { background:#e2ebfb; }
                QLineEdit, QComboBox, QSpinBox, QListWidget {
                  background:#ffffff; border:1px solid #d9e2f2; border-radius:10px; padding:7px;
                }
                QComboBox {
                  border-radius:10px;
                  padding-right: 28px;
                }
                QComboBox::drop-down {
                  subcontrol-origin: padding;
                  subcontrol-position: top right;
                  width: 28px;
                  border-left: 1px solid #d9e2f2;
                  border-top-right-radius: 10px;
                  border-bottom-right-radius: 10px;
                  background: #ffffff;
                }
                QComboBox::down-arrow {
                  width: 10px;
                  height: 10px;
                  border-left: 5px solid transparent;
                  border-right: 5px solid transparent;
                  border-top: 6px solid #0b1220;
                  background: transparent;
                }
                QSpinBox {
                  border-radius:10px;
                  padding-right: 30px;
                }
                QSpinBox::up-button {
                  subcontrol-origin: border;
                  subcontrol-position: top right;
                  width: 22px;
                  border-left: 1px solid #d9e2f2;
                  border-top-right-radius: 10px;
                  background: #ffffff;
                }
                QSpinBox::down-button {
                  subcontrol-origin: border;
                  subcontrol-position: bottom right;
                  width: 22px;
                  border-left: 1px solid #d9e2f2;
                  border-bottom-right-radius: 10px;
                  background: #ffffff;
                }
                QSpinBox::up-arrow {
                  width: 0px;
                  height: 0px;
                  border-left: 4px solid transparent;
                  border-right: 4px solid transparent;
                  border-bottom: 6px solid #0b1220;
                }
                QSpinBox::down-arrow {
                  width: 0px;
                  height: 0px;
                  border-left: 4px solid transparent;
                  border-right: 4px solid transparent;
                  border-top: 6px solid #0b1220;
                }
                QProgressBar { border:1px solid #d9e2f2; border-radius:10px; text-align:center; background:#ffffff; }
                QProgressBar::chunk { background:#4f46e5; border-radius:10px; }
                QLabel#ChipReady { background:#e8eefc; color:#31477a; border-radius:10px; padding:6px 12px; font-weight:600; }
                QFrame#DropZone { background:#f5f8ff; border:1px dashed #d0dbf0; border-radius:14px; }
                QLabel#DropTitle { color:#0b1220; font-size:14px; font-weight:700; }
                QLabel#DropSub { color:#5c6a84; }
                """
            )

    def _set_status(self, state: str):
        if state == "processing":
            self.status_chip.setText("Processing")
            self.status_chip.setStyleSheet("background:#31265c;color:#d6c9ff;border-radius:10px;padding:6px 12px;font-weight:600;")
        elif state == "done":
            self.status_chip.setText("Done")
            self.status_chip.setStyleSheet("background:#18382a;color:#8ce0b2;border-radius:10px;padding:6px 12px;font-weight:600;")
        else:
            self.status_chip.setText("Ready")
            self.status_chip.setStyleSheet("background:#1f2a45;color:#b8c7e6;border-radius:10px;padding:6px 12px;font-weight:600;")

    def select_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select images", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff)")
        self._add_files([f for f in files if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS])
        self.update_name_preview()
        self.update_preview()

    def _add_files(self, files: list[str]):
        # Merge + keep order
        existing = set(self.selected_files)
        for f in files:
            if f not in existing:
                self.selected_files.append(f)
                existing.add(f)
        self.preview_combo.clear()
        self.preview_combo.addItems([os.path.basename(f) for f in self.selected_files])
        self._rebuild_thumbnails()
        self.update_name_preview()
        self.update_preview()

    def clear_files(self):
        self.selected_files = []
        self.manual_positions_by_path = {}
        # Clear thumbnails
        while self.thumbs_layout.count():
            item = self.thumbs_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.preview_combo.clear()
        self.name_preview.clear()
        self.preview.clear()
        self._set_status("ready")

    def _rebuild_thumbnails(self):
        # Remove existing tiles
        while self.thumbs_layout.count():
            item = self.thumbs_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self.selected_files:
            return

        cols = 3
        for idx, path in enumerate(self.selected_files):
            row, col = divmod(idx, cols)
            tile = QFrame()
            tile.setObjectName("ThumbTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(6, 6, 6, 6)
            thumb_label = QLabel()
            thumb_label.setAlignment(Qt.AlignCenter)
            thumb_label.setFixedSize(120, 80)
            try:
                with Image.open(path) as im:
                    im.thumbnail((180, 120), Image.LANCZOS)
                    qimg = QImage(ImageQt(im).copy())
                    thumb_label.setPixmap(QPixmap.fromImage(qimg).scaled(120, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except Exception:
                thumb_label.setText("Preview\nunavailable")
            name_label = QLabel(os.path.basename(path))
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setWordWrap(True)

            close_btn = QToolButton()
            close_btn.setText("✕")
            close_btn.setAutoRaise(True)
            close_btn.clicked.connect(lambda _, p=path: self._remove_file(p))

            top_row = QHBoxLayout()
            top_row.addWidget(close_btn, 0, Qt.AlignRight)
            top_row.addStretch(1)
            tile_layout.addLayout(top_row)
            tile_layout.addWidget(thumb_label)
            tile_layout.addWidget(name_label)

            self.thumbs_layout.addWidget(tile, row, col)

        # "Add more" tile
        add_tile = QPushButton("+ Add more")
        add_tile.clicked.connect(self.select_images)
        self.thumbs_layout.addWidget(add_tile, (len(self.selected_files) // cols) + 1, 0)

    def _remove_file(self, path: str):
        if path in self.selected_files:
            self.selected_files.remove(path)
        self.manual_positions_by_path.pop(path, None)
        self.preview_combo.clear()
        self.preview_combo.addItems([os.path.basename(f) for f in self.selected_files])
        self._rebuild_thumbnails()
        self.update_name_preview()
        self.update_preview()

    def select_watermark(self):
        file, _ = QFileDialog.getOpenFileName(self, "Select watermark", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if file:
            self.watermark_path = file
            self.wm_name.setText(os.path.basename(file))
            self.update_preview()

    def update_name_preview(self):
        self.name_preview.clear()
        if not self.selected_files:
            return
        base = sanitize_filename(self.base_edit.text())
        try:
            start = int(self.start_edit.text())
        except Exception:
            start = 1
        ext = self.format_combo.currentText().lower()
        for i, _ in enumerate(self.selected_files[:120], start=start):
            self.name_preview.addItem(f"{base}-{i}.{ext}")

    def current_preview_file(self) -> str | None:
        if not self.selected_files:
            return None
        idx = max(0, min(self.preview_combo.currentIndex(), len(self.selected_files) - 1))
        return self.selected_files[idx]

    def _save_preview_positions(self):
        path = self.current_preview_file()
        if path:
            self.manual_positions_by_path[path] = list(self.preview.markers)

    def prev_preview_image(self):
        if self.preview_combo.count() == 0:
            return
        i = (self.preview_combo.currentIndex() - 1) % self.preview_combo.count()
        self.preview_combo.setCurrentIndex(i)

    def next_preview_image(self):
        if self.preview_combo.count() == 0:
            return
        i = (self.preview_combo.currentIndex() + 1) % self.preview_combo.count()
        self.preview_combo.setCurrentIndex(i)

    def update_preview(self):
        path = self.current_preview_file()
        if not path:
            return
        try:
            w = int(self.width_edit.text())
            h = int(self.height_edit.text())
        except Exception:
            w, h = 800, 600
        with Image.open(path) as im:
            im.thumbnail((w, h), Image.LANCZOS)
            target = im.convert("RGBA")
        tw, th = target.size

        subject_mask, xmin, xmax, ymin, ymax = estimate_subject_mask_and_bbox(target)

        manual_markers: list[tuple[float, float]] = []
        if self.enable_wm.isChecked() and self.watermark_path:
            if self.manual_wm.isChecked():
                positions = self.manual_positions_by_path.get(path, [])
                needed = self.count_spin.value()
                if len(positions) < needed:
                    positions += [(0.5, 0.5)] * (needed - len(positions))
                positions = positions[:needed]
                self.manual_positions_by_path[path] = positions
                manual_markers = positions

                # For manual mode, show the base image and render a draggable
                # watermark overlay (instead of compositing once).
                composed = target.copy()
                disp = composed.copy()
            else:
                # Auto mode: composite directly for a stable preview.
                rng_preview = random.Random(
                    hash(
                        (
                            path,
                            tw,
                            th,
                            self.count_spin.value(),
                            self.size_slider.value(),
                            self.opacity_slider.value(),
                        )
                    )
                    & 0xFFFFFFFF
                )
                composed = apply_smart_watermark(
                    target,
                    self.watermark_path,
                    self.count_spin.value(),
                    self.opacity_slider.value() / 100.0,
                    size_fraction=self.size_slider.value() / 100.0,
                    rng=rng_preview,
                )
                disp = composed.copy()
        else:
            disp = target.copy()

        # Build base preview pixmap.
        # Scale to the actual widget size to avoid clipping in full-screen.
        preview_w = max(320, int(self.preview.width()))
        preview_h = max(240, int(self.preview.height()))
        disp.thumbnail((preview_w, preview_h), Image.LANCZOS)
        qimg: QImage = ImageQt(disp).copy()
        qpm = QPixmap.fromImage(qimg)

        # Compute watermark overlay size in target coordinates.
        wm_frac = self.size_slider.value() / 100.0
        wm_w = max(1, int(tw * wm_frac))
        aspect = 1.0
        if self.watermark_path:
            try:
                with Image.open(self.watermark_path) as wm:
                    aspect = wm.height / max(1, wm.width)
            except Exception:
                aspect = 1.0
        wm_h = max(1, int(wm_w * aspect))

        overlay_qpix = None
        if self.enable_wm.isChecked() and self.watermark_path and self.manual_wm.isChecked():
            try:
                with Image.open(self.watermark_path) as wm_img:
                    # Match apply_smart_watermark logic: resize + apply opacity to alpha.
                    wm_img = wm_img.convert("RGBA")
                    wm_resized = wm_img.resize((wm_w, wm_h), Image.LANCZOS)
                    alpha = wm_resized.split()[3].point(
                        lambda p: int(p * (self.opacity_slider.value() / 100.0))
                    )
                    wm_resized.putalpha(alpha)

                    # Scale overlay to preview pixmap size.
                    scale_x = qpm.width() / max(1, tw)
                    scale_y = qpm.height() / max(1, th)
                    ov_w = max(1, int(wm_w * scale_x))
                    ov_h = max(1, int(wm_h * scale_y))
                    wm_disp = wm_resized.resize((ov_w, ov_h), Image.LANCZOS)
                    qoverlay_img = ImageQt(wm_disp).copy()
                    overlay_qpix = QPixmap.fromImage(qoverlay_img)
            except Exception:
                overlay_qpix = None

        markers = manual_markers if self.manual_wm.isChecked() else []
        self.preview.set_data(
            qpm,
            path,
            (tw, th),
            (wm_w, wm_h),
            markers,
            subject_mask=subject_mask,
            bbox=(xmin, xmax, ymin, ymax),
            watermark_pixmap=overlay_qpix,
        )

    def optimize_images(self):
        if not self.selected_files:
            QMessageBox.warning(self, "No images", "Please select images first.")
            return
        try:
            width = int(self.width_edit.text())
            height = int(self.height_edit.text())
            start_number = int(self.start_edit.text())
        except Exception:
            QMessageBox.critical(self, "Error", "Width/Height/Starting Number must be valid integers.")
            return
        output_folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if not output_folder:
            return

        self._set_status("processing")
        self.progress.setValue(0)
        self.progress.setMaximum(len(self.selected_files))

        base = sanitize_filename(self.base_edit.text())
        fmt = self.format_combo.currentText().upper()
        total_before = 0
        total_after = 0
        success = 0

        for i, file_path in enumerate(self.selected_files, start=start_number):
            try:
                total_before += os.path.getsize(file_path)
                with Image.open(file_path) as img:
                    img.thumbnail((width, height), Image.LANCZOS)
                    if self.enable_wm.isChecked() and self.watermark_path:
                        manual_positions = self.manual_positions_by_path.get(file_path) if self.manual_wm.isChecked() else None
                        img = apply_smart_watermark(
                            img,
                            self.watermark_path,
                            self.count_spin.value(),
                            self.opacity_slider.value() / 100.0,
                            size_fraction=self.size_slider.value() / 100.0,
                            manual_positions_rel=manual_positions,
                        )
                    name = f"{base}-{i}"
                    if fmt == "JPG":
                        out = os.path.join(output_folder, name + ".jpg")
                        img.convert("RGB").save(out, "JPEG", quality=95)
                    elif fmt == "PNG":
                        out = os.path.join(output_folder, name + ".png")
                        img.save(out, "PNG")
                    else:
                        out = os.path.join(output_folder, name + ".webp")
                        img.save(out, "WEBP", quality=90)
                    total_after += os.path.getsize(out)
                    success += 1
            except Exception:
                pass
            self.progress.setValue(success)
            QApplication.processEvents()

        self.before_lbl.setText(format_bytes(total_before))
        self.after_lbl.setText(format_bytes(total_after))
        self.saved_lbl.setText(format_bytes(max(0, total_before - total_after)))
        self.count_lbl.setText(str(success))
        self._set_status("done")
        QMessageBox.information(self, "Done", f"{success} images optimized.")


def main():
    app = QApplication(sys.argv)
    # Use Fusion for consistent subcontrol rendering (combobox/spinbox arrows)
    # across platforms, especially macOS where native style can ignore QSS pieces.
    app.setStyle("Fusion")
    icon = _load_app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
