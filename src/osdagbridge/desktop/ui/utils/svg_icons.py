"""
Plugin-independent SVG helpers.

``QIcon(":/x.svg")`` and QSS ``image: url(:/x.svg)`` both rely on Qt's *SVG
image-format / icon-engine plugins*.  On some installs (missing or mismatched
Qt plugins, some macOS / conda / frozen builds) those plugins do not load and
every such icon silently renders blank: toolbar buttons, lock button, combo /
spin-box arrows, checkbox ticks ...

``QSvgRenderer`` (the QtSvg module itself) does not need those plugins, so
everything here renders through it:

* ``SvgIcon``          - drop-in for ``QIcon`` that accepts ``:/...svg`` paths.
* ``qss_png_urls()``   - rewrites ``url(:/...svg)`` in a stylesheet to PNG files
                         rendered from the SVG (PNG is always supported).
* ``install_svg_fallbacks()`` - call once after QApplication is created; makes
                         every ``setStyleSheet`` call go through ``qss_png_urls``.
"""
import os
import re
import tempfile

from pathlib import Path

from PySide6.QtCore import Qt, QSize, QRectF, QFile, QIODevice, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter, QImage
from PySide6.QtSvg import QSvgRenderer

_ICON_SIZES = (16, 20, 24, 32, 48, 64)
_URL_RE = re.compile(r"url\(\s*['\"]?(:/[^)'\"]+?\.svg)['\"]?\s*\)", re.I)
_png_cache: dict = {}
_cache_dir = None


_RES_DIR = Path(__file__).resolve().parents[2] / "resources"


def _read_svg_bytes(path: str) -> QByteArray:
    """Read SVG data.  Tries the Qt resource first; if that fails (e.g. the
    compiled resources use a compression this Qt build can't read ->
    'Input/output error') falls back to the real file in ``resources/``."""
    f = QFile(path)
    if f.open(QIODevice.ReadOnly):
        data = f.readAll()
        f.close()
        if not data.isEmpty():
            return data
    if path.startswith(":/"):
        disk = _RES_DIR / path[2:]
    else:
        disk = Path(path)
    try:
        return QByteArray(disk.read_bytes())
    except OSError:
        return QByteArray()


def svg_pixmap(path: str, size: int = 64) -> QPixmap:
    """Render an SVG (resource or file path) to a transparent pixmap."""
    data = _read_svg_bytes(path)
    if data.isEmpty():
        return QPixmap()
    renderer = QSvgRenderer(data)
    if not renderer.isValid():
        return QPixmap()
    vb = renderer.defaultSize()
    if vb.width() > 0 and vb.height() > 0:
        scale = size / max(vb.width(), vb.height())
        w, h = max(1, round(vb.width() * scale)), max(1, round(vb.height() * scale))
    else:
        w = h = size
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(p, QRectF(0, 0, w, h))
    p.end()
    return QPixmap.fromImage(img)


class SvgIcon(QIcon):
    """QIcon that renders ``.svg`` resources through QSvgRenderer."""

    def __init__(self, *args):
        pixmaps = []
        if len(args) == 1 and isinstance(args[0], str) and args[0].lower().endswith(".svg"):
            for sz in _ICON_SIZES:
                pm = svg_pixmap(args[0], sz)
                if not pm.isNull():
                    pixmaps.append(pm)
            # QIcon.__init__ must be called exactly once.
            super().__init__()
            for pm in pixmaps:
                self.addPixmap(pm)
        else:
            super().__init__(*args)


def _png_for(svg_path: str) -> str:
    global _cache_dir
    if svg_path in _png_cache:
        return _png_cache[svg_path]
    if _cache_dir is None:
        _cache_dir = tempfile.mkdtemp(prefix="osdag_svg_")
    name = os.path.basename(svg_path).lower()
    size = 14 if "checked" in name else 32
    pm = svg_pixmap(svg_path, size)
    if pm.isNull():
        _png_cache[svg_path] = ""
        return ""
    out = os.path.join(_cache_dir, name.replace(".svg", f"_{size}.png"))
    pm.save(out, "PNG")
    out = out.replace("\\", "/")
    _png_cache[svg_path] = out
    return out


def qss_png_urls(qss: str) -> str:
    """Replace url(:/....svg) by url(<rendered png>) inside a stylesheet."""
    if not qss or ".svg" not in qss:
        return qss

    def _sub(m):
        png = _png_for(m.group(1))
        return f"url({png})" if png else m.group(0)

    return _URL_RE.sub(_sub, qss)


_installed = False


def install_svg_fallbacks(app=None):
    """Route every QSS (widget and application level) through qss_png_urls."""
    global _installed
    if _installed:
        return
    _installed = True
    from PySide6.QtWidgets import QWidget, QApplication

    _w_set = QWidget.setStyleSheet

    def _widget_set(self, sheet):
        return _w_set(self, qss_png_urls(sheet) if isinstance(sheet, str) else sheet)

    QWidget.setStyleSheet = _widget_set

    _a_set = QApplication.setStyleSheet

    def _app_set(self, sheet):
        return _a_set(self, qss_png_urls(sheet) if isinstance(sheet, str) else sheet)

    QApplication.setStyleSheet = _app_set
