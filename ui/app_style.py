"""Application theme entry point and shared Qt style helpers."""
from PySide6 import QtGui, QtWidgets

import paths


def load_stylesheet(*names):
    """Resolve resources in one place, including standalone plugin previews."""
    chunks = []
    for name in names:
        with open(paths.resource(name), encoding="utf-8") as source:
            chunks.append(source.read())
    return "\n".join(chunks).replace(
        "@UI_RESOURCES@", paths.resource_dir().replace("\\", "/"))


def apply_application_style(app):
    """Apply once, before constructing the first main window."""
    if app.property("qzStyleApplied"):
        return
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    colors = {
        "Window": "#1e1e1e", "WindowText": "#dddddd",
        "Base": "#383838", "AlternateBase": "#303030", "Text": "#dddddd",
        "Button": "#4a4a4a", "ButtonText": "#e8e8e8",
        "ToolTipBase": "#303033", "ToolTipText": "#eeeeee",
        "Highlight": "#565656", "HighlightedText": "#ffffff",
        "Light": "#626262", "Midlight": "#505050", "Mid": "#3e3e3e",
        "Dark": "#292929", "Shadow": "#141414", "BrightText": "#ffffff",
        "Link": "#80b8e8", "LinkVisited": "#b09cdb", "PlaceholderText": "#999999",
    }
    for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive, QtGui.QPalette.Disabled):
        for role, color in colors.items():
            palette.setColor(group, getattr(QtGui.QPalette, role), QtGui.QColor(color))
    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor("#888888"))
    app.setPalette(palette)
    app.setStyleSheet(load_stylesheet("controls.qss", "style.qss"))
    QtWidgets.QToolTip.setPalette(palette)
    app.setProperty("qzStyleApplied", True)


def set_style_property(widget, name, value):
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def activity_icon(path, size=22):
    """Supply real icon states; QSS cannot recolor an SVG's fixed strokes."""
    source = QtGui.QIcon(path).pixmap(size * 2, size * 2)
    icon = QtGui.QIcon()
    for mode, state, color in (
        (QtGui.QIcon.Normal, QtGui.QIcon.Off, "#999999"),
        (QtGui.QIcon.Normal, QtGui.QIcon.On, "#eeeeee"),
        (QtGui.QIcon.Active, QtGui.QIcon.Off, "#dddddd"),
        (QtGui.QIcon.Active, QtGui.QIcon.On, "#ffffff"),
        (QtGui.QIcon.Disabled, QtGui.QIcon.Off, "#555555"),
        (QtGui.QIcon.Disabled, QtGui.QIcon.On, "#555555"),
    ):
        pixmap = QtGui.QPixmap(source)
        painter = QtGui.QPainter(pixmap)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QtGui.QColor(color))
        painter.end()
        pixmap.setDevicePixelRatio(2)
        icon.addPixmap(pixmap, mode, state)
    return icon
