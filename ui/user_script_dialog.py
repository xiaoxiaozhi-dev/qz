"""Add a file, a tool folder, or pasted code without executing its contents."""
from pathlib import Path

from PySide6 import QtGui, QtWidgets
from .plugin_widget import set_role
from . import user_scripts
from dcc_bridge import dcc_label


class AddScriptDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, dcc='maya'):
        super().__init__(parent)
        if dcc not in user_scripts.SCRIPT_TYPES:
            raise ValueError('不支持的用户脚本宿主')
        self.dcc = dcc
        script_types = user_scripts.SCRIPT_TYPES[dcc]
        self.native_language = next((language for language in script_types if language != 'python'), None)
        self.native_suffix = script_types.get(self.native_language)
        native_label = {'maxscript': 'MAXScript', 'mel': 'MEL'}.get(self.native_language)
        languages = 'Python 或 ' + native_label if native_label else 'Python'
        self._file_filter = '脚本 (%s)' % ' '.join('*' + suffix for suffix in script_types.values())
        self._empty_folder_error = '工具文件夹中没有 %s 脚本' % ' 或 '.join(script_types.values())
        self.setWindowTitle('添加 %s 用户脚本' % dcc_label(dcc))
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance('M') * 54)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText('也用作保存工具的文件夹名称，可填写中文')
        self.file_edit = QtWidgets.QLineEdit()
        self.file_edit.setPlaceholderText('选择或粘贴脚本文件、工具文件夹路径')
        self.file_edit.editingFinished.connect(self._source_changed)
        self.browse_button = QtWidgets.QPushButton('选择文件…')
        self.browse_button.clicked.connect(self._browse)
        self.folder_button = QtWidgets.QPushButton('选择文件夹…')
        self.folder_button.clicked.connect(self._browse_folder)
        self.entry_file_combo = QtWidgets.QComboBox()
        self.entry_file_combo.currentIndexChanged.connect(self._sync_language)
        self._source_path = None
        self._suggested_entry = ''
        self.entry_edit = QtWidgets.QLineEdit()
        self.entry_edit.setPlaceholderText('留空执行整个文件；例如 main，不加括号')
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        set_role(self.error, 'error')
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText('添加')
        self.buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText('取消')
        self.buttons.accepted.connect(self._add)
        self.buttons.rejected.connect(self.reject)
        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(self.file_edit)
        file_row.addWidget(self.browse_button)
        file_row.addWidget(self.folder_button)
        import_layout = QtWidgets.QFormLayout()
        import_layout.addRow('来源路径', file_row)
        import_layout.addRow('入口脚本', self.entry_file_combo)
        self.entry_file_label = import_layout.labelForField(self.entry_file_combo)

        self.python_radio = QtWidgets.QRadioButton('Python (.py)')
        self.native_radio = (QtWidgets.QRadioButton('%s (%s)' % (native_label, self.native_suffix))
                             if native_label else None)
        self.mel_radio = self.native_radio  # Preserve the existing Maya UI API.
        self.language_group = QtWidgets.QButtonGroup(self)
        self.language_group.addButton(self.python_radio)
        if self.native_radio is not None:
            self.language_group.addButton(self.native_radio)
        self.python_radio.setChecked(True)
        self.python_radio.toggled.connect(self._suggest_entry)
        self.file_edit.textChanged.connect(self._suggest_entry)
        self.code_edit = QtWidgets.QPlainTextEdit()
        self.code_edit.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.code_edit.setPlaceholderText('在这里粘贴完整的 %s 代码' % languages)
        self.code_edit.setMinimumHeight(self.fontMetrics().lineSpacing() * 12)
        self.code_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.code_edit.textChanged.connect(self._suggest_entry)
        language_row = QtWidgets.QHBoxLayout()
        language_row.addWidget(QtWidgets.QLabel('脚本语言'))
        language_row.addWidget(self.python_radio)
        if self.native_radio is not None:
            language_row.addWidget(self.native_radio)
        language_row.addStretch()
        form = QtWidgets.QFormLayout()
        form.addRow('插件名称', self.name_edit)
        entry_form = QtWidgets.QFormLayout()
        entry_form.addRow('入口名称', self.entry_edit)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(import_layout)
        layout.addLayout(language_row)
        layout.addWidget(self.code_edit)
        layout.addLayout(entry_form)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)
        self.entry_file_combo.hide()
        self.entry_file_label.hide()
        self.script = None

    def _browse(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, '选择 %s 脚本' % dcc_label(self.dcc), '',
                                                        self._file_filter)
        if filename:
            self.file_edit.setText(filename)
            self._source_changed()

    def _browse_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, '选择工具文件夹', self.file_edit.text())
        if folder:
            self.file_edit.setText(folder)
            self._source_changed()

    def _source_changed(self, autofill=True):
        value = self.file_edit.text().strip()
        path = Path(value).expanduser() if value else None
        is_folder = path is not None and path.is_dir()
        self.entry_file_combo.setVisible(is_folder)
        self.entry_file_label.setVisible(is_folder)
        if value != self._source_path:
            self._source_path = value
            self.entry_file_combo.clear()
            self.error.clear()
            if is_folder:
                try:
                    candidates = user_scripts.script_files(path, self.dcc)
                    self.entry_file_combo.addItem('请选择入口脚本', None)
                    for candidate in candidates:
                        self.entry_file_combo.addItem(candidate.as_posix(), candidate.as_posix())
                    preferred = user_scripts.default_script(candidates)
                    if preferred is not None:
                        self.entry_file_combo.setCurrentIndex(self.entry_file_combo.findData(preferred.as_posix()))
                    if not candidates:
                        self.error.setText(self._empty_folder_error)
                except Exception as exc:
                    self.error.setText(str(exc))
        if autofill and path is not None and not self.name_edit.text().strip():
            self.name_edit.setText(path.name if is_folder else path.stem)
        self._sync_language()

    def _sync_language(self):
        value = self.file_edit.text().strip()
        path = Path(value).expanduser() if value else None
        if path is not None and path.is_dir():
            relative = self.entry_file_combo.currentData()
            path = path / relative if relative else None
        suffix = path.suffix.lower() if path is not None else ''
        if suffix in ('.py', self.native_suffix):
            (self.python_radio if suffix == '.py' else self.mel_radio).setChecked(True)

    def _suggest_entry(self):
        entry = (user_scripts.suggest_entry(self.code_edit.toPlainText())
                 if self.python_radio.isChecked() and not self.file_edit.text().strip() else '')
        if self.entry_edit.text().strip() in ('', self._suggested_entry):
            self.entry_edit.setText(entry)
        self._suggested_entry = entry

    def _add(self):
        try:
            user_scripts.validate_name(self.name_edit.text())
            has_path = bool(self.file_edit.text().strip())
            has_code = bool(self.code_edit.toPlainText().strip())
            if has_path:
                self._source_changed(autofill=False)
                path = Path(self.file_edit.text().strip()).expanduser()
                if path.is_dir() and self.entry_file_combo.currentData() is None:
                    raise ValueError('请选择工具文件夹中的入口脚本')
                self.script = user_scripts.add_script(self.name_edit.text(), str(path), self.entry_edit.text(),
                    entry_file=self.entry_file_combo.currentData() if path.is_dir() else None, dcc=self.dcc)
            elif has_code:
                self.script = user_scripts.add_code(self.name_edit.text(), self.code_edit.toPlainText(),
                    self.entry_edit.text(), 'python' if self.python_radio.isChecked() else self.native_language, dcc=self.dcc)
            else:
                raise ValueError('请选择脚本文件或工具文件夹，或直接粘贴代码')
        except Exception as exc:
            self.error.setText(str(exc))
            return
        self.accept()
