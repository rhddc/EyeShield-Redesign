"""
Custom widgets for the screening module.
"""

from PySide6.QtWidgets import (
    QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout, QDialog, QScrollArea, QStyle,
    QDateEdit, QCalendarWidget, QAbstractSpinBox, QSpinBox, QComboBox, QWidget, QFrame
)
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QTextCharFormat
from PySide6.QtCore import Qt, QSize, QEvent, QDate, QTimer


class ModernCalendarDateEdit(QDateEdit):
    """Clean date picker — dropdown arrow only, no separate button panel."""

    def __init__(self, min_date: QDate, max_date: QDate, arrow_icon_path: str, default_date: QDate = None, parent=None):
        super().__init__(parent)
        self._min_date = min_date
        self._max_date = max_date
        self._default_date = default_date or QDate(2000, 1, 1)
        self._arrow_icon_path = str(arrow_icon_path or "").replace("\\", "/")

        self.setDisplayFormat("dd/MM/yyyy")
        self.setCalendarPopup(True)
        self.setMinimumDate(min_date)
        self.setMaximumDate(max_date)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setSpecialValueText(" ")
        self.setDate(self._min_date)

        cal = QCalendarWidget(self)
        cal.setGridVisible(False)
        cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        cal.setMinimumSize(410, 320)
        cal.currentPageChanged.connect(self._sync_year_dropdown)
        self.setCalendarWidget(cal)

        def _on_cal_show(e):
            QCalendarWidget.showEvent(cal, e)
            if self.date() == self.minimumDate():
                QTimer.singleShot(0, lambda: self._sync_to_default_year())

        cal.showEvent = _on_cal_show
        QTimer.singleShot(0, self._setup_year_dropdown)
        self.apply_theme(False)

    def _setup_year_dropdown(self):
        cal = self.calendarWidget()
        if not cal:
            return

        nav = cal.findChild(QWidget, "qt_calendar_navigationbar")
        if not nav:
            QTimer.singleShot(0, self._setup_year_dropdown)
            return

        year_spin = nav.findChild(QSpinBox, "qt_calendar_yearedit")
        if not year_spin:
            return

        year_combo = nav.findChild(QComboBox, "qt_calendar_yearcombo")
        if year_combo is None:
            year_combo = QComboBox(nav)
            year_combo.setObjectName("qt_calendar_yearcombo")
            year_combo.setMinimumWidth(92)
            year_combo.setMaxVisibleItems(12)
            year_combo.setEditable(False)
            year_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

            for year in range(self._min_date.year(), self._max_date.year() + 1):
                year_combo.addItem(str(year), year)

            year_combo.currentIndexChanged.connect(self._on_year_dropdown_changed)

            nav_layout = nav.layout()
            if nav_layout is not None:
                idx = nav_layout.indexOf(year_spin)
                if idx >= 0:
                    nav_layout.insertWidget(idx, year_combo)
                else:
                    nav_layout.addWidget(year_combo)

        year_spin.hide()
        year_spin.setEnabled(False)
        year_button = nav.findChild(QWidget, "qt_calendar_yearbutton")
        if year_button is not None:
            year_button.hide()
            year_button.setEnabled(False)
        self._sync_year_dropdown()

    def _sync_to_default_year(self):
        cal = self.calendarWidget()
        if not cal:
            return
        cal.blockSignals(True)
        cal.setCurrentPage(self._default_date.year(), self._default_date.month())
        cal.blockSignals(False)
        self._sync_year_dropdown()

    def _sync_year_dropdown(self, year: int | None = None, _month: int | None = None):
        cal = self.calendarWidget()
        if not cal:
            return
        if year is None:
            year = cal.yearShown()

        nav = cal.findChild(QWidget, "qt_calendar_navigationbar")
        if not nav:
            return

        year_combo = nav.findChild(QComboBox, "qt_calendar_yearcombo")
        if not year_combo:
            return

        idx = year_combo.findData(int(year))
        if idx >= 0 and year_combo.currentIndex() != idx:
            prev_state = year_combo.blockSignals(True)
            year_combo.setCurrentIndex(idx)
            year_combo.blockSignals(prev_state)

    def _on_year_dropdown_changed(self, index: int):
        cal = self.calendarWidget()
        if not cal or index < 0:
            return
        nav = cal.findChild(QWidget, "qt_calendar_navigationbar")
        if not nav:
            return
        year_combo = nav.findChild(QComboBox, "qt_calendar_yearcombo")
        if not year_combo:
            return
        year = year_combo.itemData(index)
        if year is None:
            return
        cal.setCurrentPage(int(year), cal.monthShown())

    def apply_theme(self, dark: bool):
        if dark:
            f_bg, f_text, border, focus = "#2b3038", "#d8dee8", "#495160", "#7b92ad"
            d_bg, d_border = "#343c48", "#596577"
            c_bg, c_text, c_border = "#262c34", "#d8dee8", "#495160"
            nav_bg = "#2d3440"
            sel_bg, sel_fg = "#4f5f75", "#eaf0f7"
            today, menu_bg = "#8ea3bb", "#2a3038"
            weekend_col = "#a6b3c3"
        else:
            f_bg, f_text, border, focus = "#ffffff", "#1f2933", "#d7dde6", "#6f8aa6"
            d_bg, d_border = "#f3f6fa", "#c1ccd9"
            c_bg, c_text, c_border = "#ffffff", "#1f2933", "#dde4ed"
            nav_bg = "#f7f9fc"
            sel_bg, sel_fg = "#dbe5f0", "#1f2933"
            today, menu_bg = "#8ea6bf", "#ffffff"
            weekend_col = "#6b7787"

        arrow = self._arrow_icon_path

        self.setStyleSheet(f"""
            QDateEdit {{
                background: {f_bg};
                color: {f_text};
                border: 1.5px solid {border};
                border-radius: 6px;
                padding: 6px 36px 6px 10px;
                min-height: 28px;
                selection-background-color: {focus};
            }}
            QDateEdit:focus {{
                border: 1.5px solid {focus};
            }}
            QDateEdit::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left: 1px solid {border};
                background: {d_bg};
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            QDateEdit::down-arrow {{
                image: url("{arrow}");
                width: 10px;
                height: 10px;
            }}
        """)

        cal = self.calendarWidget()
        if not cal:
            return

        weekend_fmt = QTextCharFormat()
        weekend_fmt.setForeground(QColor(weekend_col))
        cal.setWeekdayTextFormat(Qt.DayOfWeek.Saturday, weekend_fmt)
        cal.setWeekdayTextFormat(Qt.DayOfWeek.Sunday, weekend_fmt)

        cal.setStyleSheet(f"""
            QCalendarWidget {{
                background: {c_bg};
                border: 1px solid {c_border};
                border-radius: 10px;
            }}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background: {nav_bg};
                border-bottom: 1px solid {c_border};
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                padding: 4px 6px;
            }}
            QCalendarWidget QToolButton {{
                color: {c_text};
                background: transparent;
                border: none;
                border-radius: 5px;
                font-size: 13px;
                font-weight: 600;
                padding: 4px 10px;
            }}
            QCalendarWidget QToolButton:hover {{
                background: {d_bg};
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth,
            QCalendarWidget QToolButton#qt_calendar_nextmonth {{
                qproperty-icon: none;
                font-size: 16px;
                font-weight: 700;
                padding: 2px 8px;
                color: {focus};
            }}
            QCalendarWidget QMenu {{
                background: {menu_bg};
                color: {c_text};
                border: 1px solid {c_border};
                border-radius: 6px;
                padding: 4px;
            }}
            QCalendarWidget QMenu::item:selected {{
                background: {sel_bg};
                color: {sel_fg};
            }}
            QCalendarWidget QSpinBox {{
                background: {c_bg};
                color: {c_text};
                border: 1px solid {c_border};
                border-radius: 5px;
                padding: 2px 6px;
            }}
            QCalendarWidget QComboBox#qt_calendar_yearcombo {{
                background: {c_bg};
                color: {c_text};
                border: 1px solid {c_border};
                border-radius: 5px;
                padding: 2px 20px 2px 8px;
                min-width: 76px;
            }}
            QCalendarWidget QComboBox#qt_calendar_yearcombo::drop-down {{
                border: none;
                width: 18px;
            }}
            QCalendarWidget QComboBox#qt_calendar_yearcombo::down-arrow {{
                image: url("{arrow}");
                width: 9px;
                height: 6px;
            }}
            QCalendarWidget QComboBox#qt_calendar_yearcombo QAbstractItemView {{
                background: {menu_bg};
                color: {c_text};
                border: 1px solid {c_border};
                selection-background-color: {sel_bg};
                selection-color: {sel_fg};
            }}
            QCalendarWidget QAbstractItemView {{
                background: {c_bg};
                color: {c_text};
                selection-background-color: {sel_bg};
                selection-color: {sel_fg};
                outline: none;
                gridline-color: transparent;
            }}
            QCalendarWidget QAbstractItemView:disabled {{
                color: #9ca3af;
            }}
        """)



# ── Pen annotation colour palette ─────────────────────────────────────────────
_PEN_COLORS = [
    ("#c81e1e", "Red"),
    ("#fde910", "Yellow"),
    ("#ffffff", "White"),
]


class DrawableZoomLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_pixmap = QPixmap()
        self.zoom_factor = 1.0
        self.draw_enabled = False
        self.pen_color = QColor("#c81e1e")
        self.strokes = []
        self.current_stroke = []
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_base_pixmap(self, pixmap):
        self.base_pixmap = pixmap
        self.strokes = []
        self.current_stroke = []
        self._update_display()

    def set_zoom_factor(self, factor):
        self.zoom_factor = factor
        self._update_display()

    def set_draw_enabled(self, enabled):
        self.draw_enabled = enabled
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def set_pen_color(self, color: QColor):
        self.pen_color = color

    def clear_drawings(self):
        self.strokes = []
        self.current_stroke = []
        self._update_display()

    def _map_to_image_point(self, position):
        if self.base_pixmap.isNull():
            return (0.0, 0.0)

        max_x = max(0.0, float(self.base_pixmap.width() - 1))
        max_y = max(0.0, float(self.base_pixmap.height() - 1))
        point_x = min(max(position.x() / self.zoom_factor, 0.0), max_x)
        point_y = min(max(position.y() / self.zoom_factor, 0.0), max_y)
        return (point_x, point_y)

    def _update_display(self):
        if self.base_pixmap.isNull():
            self.setPixmap(QPixmap())
            return

        canvas = self.base_pixmap.scaled(
            max(1, int(self.base_pixmap.width() * self.zoom_factor)),
            max(1, int(self.base_pixmap.height() * self.zoom_factor)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.pen_color, max(2, int(2 * self.zoom_factor)), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        for stroke in self.strokes + ([self.current_stroke] if self.current_stroke else []):
            for index in range(1, len(stroke)):
                start_x, start_y = stroke[index - 1]
                end_x, end_y = stroke[index]
                painter.drawLine(
                    int(start_x * self.zoom_factor),
                    int(start_y * self.zoom_factor),
                    int(end_x * self.zoom_factor),
                    int(end_y * self.zoom_factor),
                )

        painter.end()
        self.setPixmap(canvas)
        self.resize(canvas.size())

    def mousePressEvent(self, event):
        if self.draw_enabled and event.button() == Qt.MouseButton.LeftButton and not self.base_pixmap.isNull():
            self.current_stroke = [self._map_to_image_point(event.position())]
            self._update_display()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.draw_enabled and event.buttons() & Qt.MouseButton.LeftButton and self.current_stroke:
            self.current_stroke.append(self._map_to_image_point(event.position()))
            self._update_display()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.draw_enabled and event.button() == Qt.MouseButton.LeftButton and self.current_stroke:
            self.current_stroke.append(self._map_to_image_point(event.position()))
            self.strokes.append(self.current_stroke)
            self.current_stroke = []
            self._update_display()
            return
        super().mouseReleaseEvent(event)


class ImageZoomDialog(QDialog):
    ZOOM_STEP = 1.25

    def __init__(self, pixmap, title, parent=None):
        super().__init__(parent)
        self.original_pixmap = pixmap
        self.zoom_factor = 1.0

        self.setWindowTitle(title)
        self.resize(1100, 850)
        self.setStyleSheet("QDialog{background:#ffffff;}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        toolbar_container = QFrame()
        toolbar_container.setStyleSheet("QFrame{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;}")
        toolbar_layout = QVBoxLayout(toolbar_container)
        toolbar_layout.setContentsMargins(16, 12, 16, 12)
        toolbar_layout.setSpacing(10)

        top_bar = QHBoxLayout()
        tools_label = QLabel("DIAGNOSTIC TOOLS")
        tools_label.setStyleSheet("font-size:11px;font-weight:800;color:#64748b;letter-spacing:1px;")
        top_bar.addWidget(tools_label)
        top_bar.addStretch(1)
        
        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("font-size:13px;font-weight:700;color:#1e293b;")
        top_bar.addWidget(self.zoom_label)
        toolbar_layout.addLayout(top_bar)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        def _tool_btn(text, icon_pix=None, primary=False):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(36)
            if primary:
                btn.setStyleSheet(
                    "QPushButton{background:#2563eb;border:none;border-radius:10px;color:#ffffff;"
                    "padding:0 20px;font-size:13px;font-weight:700;}"
                    "QPushButton:hover{background:#1d4ed8;}")
            else:
                btn.setStyleSheet(
                    "QPushButton{background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;"
                    "color:#334155;padding:0 16px;font-size:13px;font-weight:600;}"
                    "QPushButton:hover{background:#f1f5f9;border-color:#cbd5e1;}")
            if icon_pix:
                btn.setIcon(self.style().standardIcon(icon_pix))
            return btn

        self.btn_in = _tool_btn("Zoom +", QStyle.StandardPixmap.SP_FileDialogContentsView)
        self.btn_out = _tool_btn("Zoom -", QStyle.StandardPixmap.SP_FileDialogContentsView)
        self.btn_reset = _tool_btn("Reset")
        
        controls_row.addWidget(self.btn_in)
        controls_row.addWidget(self.btn_out)
        controls_row.addWidget(self.btn_reset)
        controls_row.addSpacing(10)

        # Color swatches for annotation
        swatch_layout = QHBoxLayout()
        swatch_layout.setSpacing(8)
        self._swatches = []
        for _hex, _name in _PEN_COLORS:
            sw = QPushButton()
            sw.setFixedSize(28, 28)
            sw.setCursor(Qt.PointingHandCursor)
            sw.setToolTip(f"Annotate in {_name}")
            border = "3px solid #2563eb" if _hex == _PEN_COLORS[0][0] else "2px solid #cbd5e1"
            sw.setStyleSheet(f"background:{_hex};border-radius:14px;border:{border};")
            sw.clicked.connect(lambda checked=False, h=_hex: self._set_pen_color(h))
            swatch_layout.addWidget(sw)
            self._swatches.append((sw, _hex))
        
        controls_row.addLayout(swatch_layout)
        
        self.btn_clear = _tool_btn("Clear", QStyle.StandardPixmap.SP_DialogDiscardButton)
        controls_row.addWidget(self.btn_clear)
        
        controls_row.addStretch(1)
        
        self.btn_close = _tool_btn("Close", primary=True)
        controls_row.addWidget(self.btn_close)
        
        toolbar_layout.addLayout(controls_row)
        layout.addWidget(toolbar_container)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setStyleSheet(
            "QScrollArea{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;}"
            "QScrollBar:vertical{background:#f1f5f9;width:10px;border-radius:5px;margin:2px;}"
            "QScrollBar::handle:vertical{background:#cbd5e1;border-radius:5px;min-height:30px;}"
            "QScrollBar:horizontal{background:#f1f5f9;height:10px;border-radius:5px;margin:2px;}"
            "QScrollBar::handle:horizontal{background:#cbd5e1;border-radius:5px;min-width:30px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;}")
        layout.addWidget(self.scroll_area, 1)

        self.image_label = DrawableZoomLabel()
        self.scroll_area.setWidget(self.image_label)
        self.scroll_area.viewport().installEventFilter(self)
        self.image_label.installEventFilter(self)
        self.image_label.set_base_pixmap(self.original_pixmap)

        # Connections
        self.btn_in.clicked.connect(self.zoom_in)
        self.btn_out.clicked.connect(self.zoom_out)
        self.btn_reset.clicked.connect(self.reset_zoom)
        self.btn_clear.clicked.connect(self.clear_drawings)
        self.btn_close.clicked.connect(self.accept)

        self._update_preview()

    def eventFilter(self, watched, event):
        if watched in (self.scroll_area.viewport(), self.image_label) and event.type() == QEvent.Type.Wheel:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            elif event.angleDelta().y() < 0:
                self.zoom_out()
            return True
        return super().eventFilter(watched, event)

    def _update_preview(self):
        if self.original_pixmap.isNull():
            self.image_label.setPixmap(QPixmap())
            return None
        self.image_label.set_zoom_factor(self.zoom_factor)
        self.zoom_label.setText(f"{int(self.zoom_factor * 100)}%")
        return self.image_label.pixmap().size()

    def _set_zoom_centered(self, new_factor):
        old_factor = self.zoom_factor
        if abs(old_factor - new_factor) < 1e-5:
            return
            
        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()
        
        view_w = self.scroll_area.viewport().width()
        view_h = self.scroll_area.viewport().height()
        img_w = self.image_label.width() or 1
        img_h = self.image_label.height() or 1
        
        # Point in image coordinates that is currently at the center of the viewport
        if h_bar.maximum() > 0:
            center_x = h_bar.value() + view_w / 2
        else:
            center_x = img_w / 2

        if v_bar.maximum() > 0:
            center_y = v_bar.value() + view_h / 2
        else:
            center_y = img_h / 2

        rel_x = center_x / img_w
        rel_y = center_y / img_h
        
        self.zoom_factor = new_factor
        new_size = self._update_preview()
        
        # Adjust scrollbars
        if new_size:
            new_img_w, new_img_h = new_size.width(), new_size.height()
            h_bar.setValue(int(rel_x * new_img_w - view_w / 2))
            v_bar.setValue(int(rel_y * new_img_h - view_h / 2))

    def zoom_in(self):
        self._set_zoom_centered(min(10.0, self.zoom_factor * self.ZOOM_STEP))

    def zoom_out(self):
        self._set_zoom_centered(max(0.1, self.zoom_factor / self.ZOOM_STEP))

    def reset_zoom(self):
        self._set_zoom_centered(1.0)

    def toggle_draw_mode(self, enabled):
        self.image_label.set_draw_enabled(enabled)

    def clear_drawings(self):
        self.image_label.clear_drawings()

    def _set_pen_color(self, hex_color: str):
        self.image_label.set_pen_color(QColor(hex_color))
        for sw, h in self._swatches:
            border = "3px solid #2563eb" if h == hex_color else "2px solid #cbd5e1"
            sw.setStyleSheet(f"background:{h};border-radius:14px;border:{border};")
        # Clicking a color automatically activates draw mode.
        self.image_label.set_draw_enabled(True)


class ClickableImageLabel(QLabel):
    def __init__(self, empty_text="", viewer_title="Image Viewer", parent=None):
        super().__init__(empty_text, parent)
        self.viewer_title = viewer_title
        self.full_pixmap = QPixmap()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.open_badge = QLabel(self)
        self.open_badge.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon).pixmap(16, 16))
        self.open_badge.setFixedSize(28, 28)
        self.open_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.open_badge.setStyleSheet(
            "background: rgba(255, 255, 255, 0.92);"
            "border-radius: 14px;"
            "border: 1px solid #b8c9dd;"
        )
        self.open_badge.hide()

    def set_viewable_pixmap(self, pixmap, max_width, max_height):
        self.full_pixmap = pixmap
        scaled = pixmap.scaled(
            max_width,
            max_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
        self.setText("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to open and zoom")
        self.open_badge.show()
        self.open_badge.raise_()
        self._position_badge()

    def clear_view(self, text):
        self.full_pixmap = QPixmap()
        self.setPixmap(QPixmap())
        self.setText(text)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip("")
        self.open_badge.hide()

    def resizeEvent(self, event):
        self._position_badge()
        super().resizeEvent(event)

    def _position_badge(self):
        self.open_badge.move(
            max(8, self.width() - self.open_badge.width() - 10),
            max(8, self.height() - self.open_badge.height() - 10),
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.full_pixmap.isNull():
            dialog = ImageZoomDialog(self.full_pixmap, self.viewer_title, self)
            dialog.exec()
            return
        super().mousePressEvent(event)
