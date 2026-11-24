"""中台动作库卡片。只访问磁盘和桥接客户端，不导入 Maya。"""

import os

from PySide6 import QtCore, QtGui, QtWidgets
import paths
from ui import plugin_widget


def fuzzy_score(query, candidate):
    query, lowered = query.casefold(), candidate.casefold()
    score, cursor, last, consecutive = 0, 0, -2, 0
    for char in query:
        position = lowered.find(char, cursor)
        if position < 0:
            return 0
        consecutive = consecutive + 5 if position == last + 1 else 0
        score += 10 + consecutive
        if position == 0:
            score += 40
        elif lowered[position - 1] in "_- ":
            score += 20
        last, cursor = position, position + 1
    if query in lowered:
        score += 50
    return score


class _CollapseLine(QtWidgets.QAbstractButton):
    """只绘制细线，保留原生点击和键盘操作，不绘制按钮背景或箭头。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setFixedHeight(12)

    def sizeHint(self):
        return QtCore.QSize(0, 12)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        role = QtGui.QPalette.Midlight if self.underMouse() or self.hasFocus() else QtGui.QPalette.Mid
        painter.setPen(QtGui.QPen(self.palette().color(role), 1))
        painter.drawLine(0, self.height() // 2, self.width() - 1, self.height() // 2)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class _Signals(QtCore.QObject):
    finished = QtCore.Signal(object)


class _Request(QtCore.QRunnable):
    def __init__(self, client, action, params):
        super().__init__()
        self.client, self.action, self.params = client, action, params
        self.signals = _Signals()

    def run(self):
        try:
            result = self.client.call(self.action, timeout=600, **self.params)
        except Exception as error:
            result = {"ok": False, "error": str(error), "request_failed": True}
        self.signals.finished.emit(result)


class _AnimWidget(QtWidgets.QWidget, plugin_widget.BasePluginWidget):
    def __init__(self, client, log, parent=None):
        super().__init__(parent)
        self.client, self.log = client, log
        self.action_label = "JS 动作工具"
        self.setObjectName("JsAnimToolsWidget")
        self.library_dir = paths.local_data_dir("js_anim_pose_json")
        self._files, self._queue, self._errors = [], [], []
        self._busy, self._job, self._current = False, None, None
        self._initial_job, self._has_requests = None, False
        self._completed, self._total = 0, 0
        self._scene = ""

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.controls = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(self.controls)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        layout.addWidget(self.controls)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("搜索动作，回车导入选中项")
        self.search_edit.returnPressed.connect(self._import_selected)
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._filter_files)
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())
        form.addWidget(self.search_edit)

        self.tabs = QtWidgets.QTabWidget()
        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.file_list.setMinimumHeight(160)
        self.file_list.itemActivated.connect(lambda _item: self._import_selected())
        self.layer_list = QtWidgets.QListWidget()
        self.layer_list.itemClicked.connect(self._activate_item)
        self.layer_list.itemActivated.connect(self._activate_item)
        self.tabs.addTab(self.file_list, "动作文件")
        self.tabs.addTab(self.layer_list, "场景图层")
        form.addWidget(self.tabs)

        self.import_button = QtWidgets.QPushButton("导入选中")
        self.import_button.clicked.connect(self._import_selected)
        self.delete_button = QtWidgets.QPushButton("删除图层")
        self.delete_button.clicked.connect(self._delete_layer)
        self._row(form, self.import_button, self.delete_button)

        self.refresh_button = QtWidgets.QPushButton("刷新")
        self.refresh_button.clicked.connect(self._refresh)
        form.addWidget(self.refresh_button)

        # 无标题细线只展开控件，不执行导出；每次构造默认收起。
        self.export_toggle = _CollapseLine()
        self.export_toggle.setAccessibleName("展开导出操作")
        form.addWidget(self.export_toggle)
        self.export_panel = QtWidgets.QWidget()
        export_layout = QtWidgets.QVBoxLayout(self.export_panel)
        export_layout.setContentsMargins(0, 0, 0, 0)
        self._store = QtCore.QSettings('QzTools', 'JsAnimTools')
        self.template_edit = QtWidgets.QLineEdit()
        self.template_edit.setPlaceholderText("骨架模板 FBX（可选，F1 / F2 / M1 / M2）")
        self.template_edit.setAccessibleName("骨架模板 FBX")
        self.template_edit.setText(self._store.value('export_template', '', type=str))
        self.template_edit.setToolTip(self.template_edit.text())
        self.template_edit.textChanged.connect(self._remember_template)
        self.template_browse = self.template_edit.addAction(
            self.style().standardIcon(QtWidgets.QStyle.SP_DirOpenIcon), QtWidgets.QLineEdit.TrailingPosition)
        self.template_browse.setText("选择骨架模板 FBX")
        self.template_browse.triggered.connect(self._browse_template)
        export_layout.addWidget(self.template_edit)
        self.export_button = QtWidgets.QPushButton("导出 JSON…")
        self.export_button.clicked.connect(self._export_current)
        self.batch_export_button = QtWidgets.QPushButton("批量导出 JSON…")
        self.batch_export_button.clicked.connect(self._export_fbx)
        self._row(export_layout, self.export_button, self.batch_export_button)
        form.addWidget(self.export_panel)
        self.export_panel.hide()
        self.export_toggle.toggled.connect(self._toggle_exports)
        self.progress = QtWidgets.QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)

        self.refresh_library()
        self._fill_layers({"layers": []})
        if self.client is not None and getattr(self.client, "connected", False):
            QtCore.QTimer.singleShot(0, self._load_initial_layers)

    def _toggle_exports(self, expanded):
        self.export_panel.setVisible(expanded)
        self.export_toggle.setAccessibleName("收起导出操作" if expanded else "展开导出操作")
        self.updateGeometry()

    def _load_initial_layers(self):
        if self._has_requests:
            return
        self._initial_job = _Request(self.client, "js_anim_layers", {})
        self._initial_job.signals.finished.connect(self._on_initial_layers)
        QtCore.QThreadPool.globalInstance().start(self._initial_job)

    @QtCore.Slot(object)
    def _on_initial_layers(self, response):
        self._initial_job = None
        data = response.get("data")
        if not self._has_requests and response.get("ok") and isinstance(data, dict):
            self._fill_layers(data)

    @staticmethod
    def _row(layout, *widgets):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        for index, widget in enumerate(widgets):
            row.addWidget(widget, 1 if index == 0 else 0)
        layout.addLayout(row)

    def _message(self, message, error=False):
        self._write_log(message, error)

    def format_log_result(self, action, response):
        if response.get("ok"):
            return [("info", response.get("data", {}).get("message", "完成"))]
        return [("error", str(response.get("error") or "未知错误"))]

    def _write_log(self, message, error=False):
        if self.log is not None:
            result_logger = getattr(self, "log_result", None)
            if callable(result_logger):
                result_logger("js_anim_tools", {"ok": not error,
                    "data": {"message": message}, "error": message})
            else:
                method = self.log.error if error else self.log.info
                method("[JS 动作工具] " + message)

    def refresh_library(self):
        folder = self.library_dir
        self._files = []
        try:
            if not folder or not os.path.isdir(folder):
                raise ValueError("固定动作目录不存在：" + folder)
            self._files = sorted(entry.path for entry in os.scandir(folder)
                                 if entry.is_file() and entry.name.lower().endswith(".json"))
            self._message("找到 %d 个动作；导入前在 Maya 中选择骨架根节点" % len(self._files))
        except (OSError, ValueError) as error:
            self._message(str(error), error=True)
        self._filter_files()

    def _filter_files(self):
        query = self.search_edit.text().strip()
        self.file_list.clear()
        ranked = [(fuzzy_score(query, os.path.basename(path)), path) for path in self._files]
        ranked.sort(key=lambda pair: (-pair[0], os.path.basename(pair[1]).casefold()))
        for score, path in ranked:
            if query and not score:
                continue
            item = QtWidgets.QListWidgetItem(os.path.splitext(os.path.basename(path))[0])
            item.setData(QtCore.Qt.UserRole, path)
            item.setToolTip(path)
            self.file_list.addItem(item)
        if self.file_list.count():
            self.file_list.setCurrentRow(0)
        self.tabs.setTabText(0, "动作文件 (%d)" % self.file_list.count())

    def _refresh(self):
        self.refresh_library()
        self._refresh_layers()

    def _refresh_layers(self):
        self._start([("js_anim_layers", {}, "刷新场景图层")])

    def _import_paths(self, filenames):
        if not filenames:
            self._message("请先选择动作文件", error=True)
            return
        self._start([("js_anim_import", {"root_node": None, "json_path": path},
                      "导入 " + os.path.basename(path)) for path in filenames])

    def _import_selected(self):
        if self._search_timer.isActive():
            self._search_timer.stop()
            self._filter_files()
        self._import_paths([item.data(QtCore.Qt.UserRole) for item in self.file_list.selectedItems()])

    def _fill_layers(self, data):
        self._scene = data.get("scene", self._scene)
        self.layer_list.clear()
        base = QtWidgets.QListWidgetItem("基础动画")
        base.setData(QtCore.Qt.UserRole, None)
        self.layer_list.addItem(base)
        active = base
        for layer in data.get("layers", []):
            item = QtWidgets.QListWidgetItem("%s  [%s–%s]" % (layer["name"], layer["start"], layer["end"]))
            item.setData(QtCore.Qt.UserRole, layer["layer"])
            self.layer_list.addItem(item)
            if layer.get("weight", 0) > 0:
                active = item
        self.layer_list.setCurrentItem(active)

    def _activate_item(self, item):
        if item is not None:
            self._start([("js_anim_activate", {"layer": item.data(QtCore.Qt.UserRole)},
                          "切换 " + item.text())])

    def _dialog_anchor(self):
        widget, proxy = self, None
        while widget is not None and proxy is None:
            proxy = widget.graphicsProxyWidget()
            widget = widget.parentWidget()
        parent = self.window() if proxy is None else None
        center = self.mapToGlobal(self.rect().center()) if proxy is None else QtGui.QCursor.pos()
        bounds = None
        if proxy is not None and proxy.scene() is not None:
            views = [view for view in proxy.scene().views() if view.isVisible()]
            if views:
                view = next((view for view in views if view.viewport().underMouse()), views[0])
                viewport = view.viewport()
                parent = view.window()
                center = viewport.mapToGlobal(view.mapFromScene(proxy.mapToScene(proxy.boundingRect().center())))
                bounds = QtCore.QRect(viewport.mapToGlobal(QtCore.QPoint()), viewport.size())
        screen_point = center if bounds is None else QtCore.QPoint(
            max(bounds.left(), min(center.x(), bounds.right())),
            max(bounds.top(), min(center.y(), bounds.bottom())))
        screen = QtGui.QGuiApplication.screenAt(screen_point) or QtGui.QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        bounds = available if bounds is None else bounds.intersected(available)
        return parent, center, available if bounds.isEmpty() else bounds

    def _confirm(self, title, message):
        parent, center, bounds = self._dialog_anchor()
        # Use the real application window: a card parent makes Qt embed the
        # message box in QGraphicsProxyWidget and misinterpret its coordinates.
        dialog = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, title, message,
                                      QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, parent)
        dialog.setTextFormat(QtCore.Qt.PlainText)
        dialog.button(QtWidgets.QMessageBox.Yes).setText("确定")
        dialog.button(QtWidgets.QMessageBox.No).setText("取消")
        dialog.setDefaultButton(QtWidgets.QMessageBox.No)
        dialog.setEscapeButton(QtWidgets.QMessageBox.No)
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        try:
            dialog.adjustSize()
            dialog.show()
            # Measure the native frame after showing so its title bar also
            # stays inside the visible canvas, including at non-unit zoom.
            frame = dialog.frameGeometry()
            left = max(bounds.left(), min(center.x() - frame.width() // 2,
                                          bounds.right() - frame.width() + 1))
            top = max(bounds.top(), min(center.y() - frame.height() // 2,
                                        bounds.bottom() - frame.height() + 1))
            dialog.move(left, top)
            return dialog.exec() == QtWidgets.QMessageBox.Yes
        finally:
            dialog.deleteLater()

    def _delete_layer(self):
        item = self.layer_list.currentItem()
        layer = item.data(QtCore.Qt.UserRole) if item else None
        if not layer:
            self._message("请选择要删除的动作图层", error=True)
            return
        if self._confirm("删除动作图层", "删除“%s”？可在 Maya 中撤销。" % item.text()):
            self._start([("js_anim_delete", {"layer": layer}, "删除图层")])

    def _export_current(self):
        template = self._selected_template()
        stem = os.path.splitext(os.path.basename(self._scene))[0] or "animation"
        name, accepted = QtWidgets.QInputDialog.getText(
            self._dialog_anchor()[0], "导出动作 JSON", "动作文件名：", text=stem)
        if not accepted:
            return
        name = name.strip()
        if not name or name in (".", "..") or any(char in name for char in '<>:"/\\|?*'):
            self._message("请输入有效的动作文件名，不要包含目录或特殊字符", error=True)
            return
        if not name.lower().endswith(".json"):
            name += ".json"
        path = os.path.join(self.library_dir, name)
        if os.path.exists(path) and not self._confirm("覆盖动作文件", "覆盖已有文件“%s”？" % name):
            return
        self._start([("js_anim_export", {"root_node": None, "json_path": path, "overwrite": True,
                                       "template_path": template}, "导出当前动作")])

    def _remember_template(self, path):
        self._store.setValue('export_template', path)
        self.template_edit.setToolTip(path)

    def _browse_template(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._dialog_anchor()[0], "选择骨架模板 FBX", self.template_edit.text().strip(), "FBX (*.fbx *.FBX)")
        if path:
            self.template_edit.setText(path)

    def _selected_template(self):
        path = self.template_edit.text().strip()
        return os.path.abspath(os.path.expanduser(path)) if path else None

    def _export_fbx(self):
        template = self._selected_template()
        folder = self.library_dir
        if not os.path.isdir(folder):
            self._message("固定动作目录不存在：" + folder, error=True)
            return
        filenames, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self._dialog_anchor()[0], "选择 FBX 文件", "", "FBX (*.fbx *.FBX)")
        if not filenames:
            return
        stems = [os.path.splitext(os.path.basename(path))[0] for path in filenames]
        if len(set(name.casefold() for name in stems)) != len(stems):
            self._message("所选 FBX 文件存在同名输出，请分开处理或先重命名", error=True)
            return
        existing = any(os.path.exists(os.path.join(folder, stem + ".json")) for stem in stems)
        message = "将导出所选 FBX 动作。"
        if template:
            message += "\n模板：%s。识别到有效体型时转换骨架，否则常规导出。" % os.path.basename(template)
        else:
            message += "\n未选择模板，按原骨架常规导出。"
        if existing:
            message += "\n输出目录中的同名 JSON 将被覆盖。"
        if not self._confirm("批量导出", message):
            return
        self._start([("js_anim_export_fbx", {"fbx_path": path, "output_folder": folder, "overwrite": existing,
                                           "template_path": template},
                      "导出 " + os.path.basename(path)) for path in filenames])

    def _start(self, tasks):
        if self._busy:
            return
        if self.client is None:
            self._message("请先连接 Maya", error=True)
            return
        self._has_requests = True
        self._queue = list(tasks)
        self._total, self._completed, self._errors = len(tasks), 0, []
        self._busy = True
        self.controls.setEnabled(False)
        self.progress.setRange(0, self._total)
        self.progress.setValue(0)
        self.progress.show()
        self._next()

    def _next(self):
        if not self._queue:
            self._busy = False
            self._job = None
            self.controls.setEnabled(True)
            self.progress.hide()
            if self._total > 1:
                self._message("处理完成：%d 成功，%d 失败。详情见日志。" % (
                    self._completed - len(self._errors), len(self._errors)), error=bool(self._errors))
            return
        self._current = self._queue.pop(0)
        action, params, label = self._current
        self.progress.setFormat(label + " %v/%m")
        self._message("%s (%d/%d)…" % (label, self._completed + 1, self._total))
        self._job = _Request(self.client, action, params)
        self._job.signals.finished.connect(self._on_result)
        QtCore.QThreadPool.globalInstance().start(self._job)

    @QtCore.Slot(object)
    def _on_result(self, response):
        action, _params, label = self._current
        if not response.get("ok"):
            error = str(response.get("error") or "未知错误")
            if "unknown action" in error:
                error = "请在 Maya 连接设置中重新执行启动代码，或重载动作库后重试。"
            if response.get("request_failed"):
                self._queue.clear()
                self._total = self._completed + 1
                error += "；已停止后续任务。Maya 可能仍在执行，请等待完成并刷新后再重试。"
            self._errors.append(error)
            self._message(label + "失败：" + error.rstrip().splitlines()[-1], error=True)
            if "\n" in error and self.log is not None:
                self._write_log(error, error=True)
        else:
            data = response.get("data") or {}
            if "layers" in data:
                self._fill_layers(data)
            if action == "js_anim_import":
                # 批次始终绑定第一次成功导入时的骨架，避免中途改选影响后续动作。
                for queued_action, queued_params, _label in self._queue:
                    if queued_action == "js_anim_import" and not queued_params.get("root_node") and data.get("root_node"):
                        queued_params["root_node"] = data["root_node"]
                self.tabs.setCurrentIndex(1)
                self._message("%s：%d 个节点，%d 个关键帧，%d 个跳过" % (
                    label, data["imported_nodes"], data["total_keys"], len(data.get("skipped", []))))
            elif action in ("js_anim_export", "js_anim_export_fbx"):
                self.refresh_library()
                self._message("已导出 %d 个节点、%d 帧 → %s" % (
                    data["node_count"], data["frame_count"], data["json_path"]))
                if data.get("body_type"):
                    self._message("标准体型模板：" + data["body_type"])
                elif data.get("export_mode") == "regular":
                    self._message("常规导出：未使用体型模板")
            else:
                self._message(label + "完成")
        self._completed += 1
        self.progress.setValue(self._completed)
        self._next()


def create_widget(client, log, parent=None):
    return _AnimWidget(client, log, parent)
