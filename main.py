# txconverter 2.0 start #

import os
import re
import sys
import subprocess
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6 import QtCore, QtGui, QtWidgets
try:
    from shiboken6 import wrapInstance
except ImportError:
    from shiboken6 import wrapInstance

def get_user_settings_path():
    """
    Returns a path like:
    - Windows:  C:/Users/<You>/AppData/Roaming/TxConverter/txconverter_settings.json
    - Other OS: /home/<You>/TxConverter/txconverter_settings.json
    """
    appdata = os.environ.get('APPDATA')  # Typically Windows
    if not appdata:
        appdata = os.path.expanduser("~")
    settings_folder = os.path.join(appdata, "TxConverter")
    os.makedirs(settings_folder, exist_ok=True)
    return os.path.join(settings_folder, "txconverter_settings.json")

# -----------------------------------------------------------
# Helper: Detect which ACES version the OCIO file is
# -----------------------------------------------------------
def detect_aces_version(config_path):
    """
    Reads the .ocio file and tries to distinguish ACES 1.0.3 vs. 1.3
    by looking for certain indicators. Returns "1.3", "1.0.3", or "unknown".
    """
    if not config_path or not os.path.isfile(config_path):
        return "unknown"

    version_13_markers = ["ocio_profile_version: 2.2", "ACES 1.3", "ACES 1.1", "ACES 1.0 - SDR Video"]
    version_10_markers = ["An ACES config generated from python", "ACES - ACES2065-1", "Output - Rec.709"]

    # Read first ~500 lines
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for _ in range(500):
                line = f.readline()
                if not line:
                    break
                check = line.strip().lower()

                if any(m.lower() in check for m in version_13_markers):
                    return "1.3"
                if any(m.lower() in check for m in version_10_markers):
                    return "1.0.3"
    except:
        pass

    return "unknown"


# -----------------------------------------------------------
# Worker Class for Texture Conversion
# -----------------------------------------------------------
class TextureWorker(QtCore.QObject):
    progressSignal = QtCore.Signal(int)   # emits the number of textures processed
    logSignal = QtCore.Signal(str)        # emits log messages
    finishedSignal = QtCore.Signal()      # emitted when done

    def __init__(
        self,
        textures,
        rename_to_acescg,
        add_suffix_selected,
        use_compression,
        use_renderman,
        hdri_mode=False,
        parent=None,
        use_renderman_bumprough=False,
        userSettings=None,
        use_houdini_rat=False
    ):
        super(TextureWorker, self).__init__(parent)
        self.textures                 = textures
        self.rename_to_acescg         = rename_to_acescg
        self.add_suffix_selected      = add_suffix_selected
        self.use_compression          = use_compression
        self.use_renderman            = use_renderman
        self.hdri_mode                = hdri_mode
        self.use_renderman_bumprough  = use_renderman_bumprough
        self.use_houdini_rat          = use_houdini_rat

        self.userSettings   = userSettings or {}
        self.env_var_names  = self.userSettings.get("env_var_names", {
            "imaketx":  "IMAKETX_PATH",
            "arnold":   "MAKETX_PATH",
            "renderman":"RMANTREE",
            "ocio":     "OCIO",
            "hfs":      "HFS"
        })

        # batch size
        self.batch_size = int(self.userSettings.get("batch_size", 6))


    def run(self):
        total = len(self.textures)

        processed = 0

        # Use self.batch_size from user settings, not a hardcoded 6
        for i in range(0, total, self.batch_size):
            batch = self.textures[i : i + self.batch_size]
            with ThreadPoolExecutor(max_workers=self.batch_size) as executor:
                futures = {
                    executor.submit(self.convert_texture, tex, cs, opts): (tex, cs)
                    for (tex, cs, opts) in batch
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self.logSignal.emit(f"Error during conversion: {e}")
                    processed += 1
                    self.progressSignal.emit(processed)
        self.finishedSignal.emit()

    def convert_texture(self, texture, color_space, additional_options):
        self.logSignal.emit(f"Starting conversion for {os.path.basename(texture)}...")

        # ── resolve env-var names set by the UI ──────────────────────────
        n = self.env_var_names
        imaketx_var  = n.get("imaketx",   "IMAKETX_PATH")
        arnold_var   = n.get("arnold",    "MAKETX_PATH")
        rman_var     = n.get("renderman", "RMANTREE")
        ocio_var     = n.get("ocio",      "OCIO")
        hfs_var      = n.get("hfs",       "HFS")

        imaketx_path = (
            os.environ.get(imaketx_var) or
            (os.path.join(os.environ.get(hfs_var, ""), "bin", "imaketx")
                if os.environ.get(hfs_var) else None) or
            "imaketx"
        )
        arnold_path    = os.environ.get(arnold_var, "maketx")
        color_config   = os.environ.get(ocio_var, "")
        renderman_root = os.environ.get(rman_var, "")
        txmake_path    = (os.path.join(renderman_root, "bin", "txmake")
                          if renderman_root else None)
        # ----------------------------------------------------------------

        aces_version = detect_aces_version(color_config)
        out_folder   = os.path.dirname(texture)
        base_name, ext_with_dot = os.path.splitext(os.path.basename(texture))
        ext = ext_with_dot.lower()[1:]

        # skip extensions we've already produced
        if ext in ["tex", "tx", "b2r", "rat"]:
            self.logSignal.emit(f"Skipping already-processed file: {texture}")
            return

        # determine suffix
        if self.rename_to_acescg:
            base_name = re.sub(r'(_raw|_srgb_texture|_lin_srgb|_acescg)$',
                               '', base_name, flags=re.IGNORECASE)
            suffix = "_acescg"
        else:
            if self.add_suffix_selected:
                if not re.search(r'(_raw|_srgb_texture|_lin_srgb|_acescg)$',
                                 base_name, flags=re.IGNORECASE):
                    suffix = f"_{color_space}"
                else:
                    suffix = ""
            else:
                suffix = ""

        # detect special maps
        is_displacement = re.search(r'_disp|_displacement|_zdisp',
                                    base_name, re.IGNORECASE)
        is_bump         = re.search(r'_bump|_height',
                                    base_name, re.IGNORECASE)
        is_normal       = re.search(r'_normal|_nrm|_norm(?=[^a-z])',
                                    base_name, re.IGNORECASE)

        # choose bit depth
        if is_displacement:
            bit_depth = 'float'
        else:
            if self.hdri_mode and color_space != 'raw':
                bit_depth = 'float'
            else:
                if ext in ['jpg', 'jpeg', 'gif', 'bmp']:
                    bit_depth = 'uint8'
                elif ext in ['png', 'tif', 'tiff', 'exr']:
                    bit_depth = 'half'
                else:
                    bit_depth = 'uint16'

        # -----------------------------------------------------------------
        # Houdini .rat via imaketx
        # -----------------------------------------------------------------
        if self.use_houdini_rat:
            out_file = os.path.join(out_folder, f"{base_name}{suffix}.rat")
            rat_cmd  = [imaketx_path, "-v", "--format", "RAT"]

            if color_space not in ["raw", "acescg"]:
                if color_config:
                    rat_cmd += ["--colormanagement", "ocio"]
                    if color_space == "lin_srgb":
                        src = ("Linear Rec.709 (sRGB)"
                               if aces_version == "1.3" else "lin_srgb")
                    else:
                        src = ("sRGB - Texture"
                               if aces_version == "1.3" else "srgb_texture")
                    rat_cmd += ["--colorconvert", src, "ACEScg"]
                else:
                    rat_cmd += ["--colormanagement", "builtin"]

            rat_cmd += [texture, out_file]

            self.logSignal.emit("imaketx command: " + " ".join(rat_cmd))
            try:
                res = subprocess.run(rat_cmd, shell=False,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
                if res.stdout:
                    self.logSignal.emit("imaketx output: " +
                                        res.stdout.decode().strip())
                if res.stderr:
                    self.logSignal.emit("imaketx errors: " +
                                        res.stderr.decode().strip())
                self.logSignal.emit(f"Converted to .rat: {texture} -> {out_file}")
            except subprocess.CalledProcessError as e:
                self.logSignal.emit(f"Failed to convert {texture} to .rat: {e}")
            return

        # -----------------------------------------------------------------
        # RenderMan .tex via txmake
        # -----------------------------------------------------------------
        if self.use_renderman and txmake_path:
            self.logSignal.emit(f"Converting {os.path.basename(texture)} to RenderMan .tex...")
            out_base = base_name + suffix
            tx_cmd = [txmake_path, "-format", "openexr"]
            if self.use_compression:
                tx_cmd += ["-compression", "zip"]
            if bit_depth == 'half':
                tx_cmd += ["-half"]
            elif bit_depth == 'float':
                tx_cmd += ["-float"]
            tx_cmd += ["-resize", "round-", "-mode", "periodic"]

            if color_space not in ["raw", "acescg"] and color_config:
                if color_space == "lin_srgb":
                    if aces_version == "1.3":
                        tx_cmd += ["-ocioconvert",
                                   "Linear Rec.709 (sRGB)", "ACEScg"]
                    else:
                        tx_cmd += ["-ocioconvert", "lin_srgb",
                                   "ACES - ACEScg"]
                elif color_space == "srgb_texture":
                    if aces_version == "1.3":
                        tx_cmd += ["-ocioconvert",
                                   "sRGB - Texture", "ACEScg"]
                    else:
                        tx_cmd += ["-ocioconvert", "srgb_texture",
                                   "ACES - ACEScg"]

            if self.use_renderman_bumprough and (is_bump or is_normal):
                out_ext = ".b2r"
                out_file = os.path.join(out_folder, out_base + out_ext)
                if is_normal:
                    tx_cmd += ["-bumprough", "2", "0", "1", "0", "0", "1"]
                else:
                    tx_cmd += ["-bumprough", "2", "0", "0", "0", "0", "1"]
            else:
                out_ext = f".{ext}.tex"
                out_file = os.path.join(out_folder, out_base + out_ext)

            tx_cmd += [texture, out_file]
            self.logSignal.emit("txmake command: " + " ".join(tx_cmd))
            try:
                result = subprocess.run(tx_cmd, shell=False,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
                out_msg = result.stdout.decode('utf-8').strip()
                err_msg = result.stderr.decode('utf-8').strip()
                if out_msg:
                    self.logSignal.emit("txmake output: " + out_msg)
                if err_msg:
                    self.logSignal.emit("txmake errors: " + err_msg)
                self.logSignal.emit(f"Converted to {out_ext}: {texture} -> {out_file}")
            except subprocess.CalledProcessError as e:
                self.logSignal.emit(f"Failed to convert {texture} to .tex: {e}")
            return

        # -----------------------------------------------------------------
        # Arnold .tx via maketx
        # -----------------------------------------------------------------
        arnold_out = os.path.join(out_folder, f"{base_name}{suffix}.tx")
        comp_flag = []
        if self.use_compression and not is_displacement:
            comp_flag = ["--compression", "dwaa"]

        cmd = [
            arnold_path, "-v",
            "-o", arnold_out,
            "-u",
            "--format", "exr",
            "-d", bit_depth
        ] + comp_flag + ["--oiio", texture]

        if color_space not in ["raw", "acescg"] and color_config:
            cmd += ["--colorconfig", color_config]
            if color_space == "lin_srgb":
                if aces_version == "1.3":
                    cmd += ["--colorconvert",
                            "Linear Rec.709 (sRGB)", "ACEScg"]
                else:
                    cmd += ["--colorconvert", "lin_srgb",
                            "ACES - ACEScg"]
            elif color_space == "srgb_texture":
                if aces_version == "1.3":
                    cmd += ["--colorconvert",
                            "sRGB - Texture", "ACEScg"]
                else:
                    cmd += ["--colorconvert", "srgb_texture",
                            "ACES - ACEScg"]

        self.logSignal.emit(f"Converting {os.path.basename(texture)} to Arnold .tx...")
        try:
            result = subprocess.run(cmd, shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            if result.stdout:
                self.logSignal.emit("maketx output: " +
                                    result.stdout.decode('utf-8').strip())
            if result.stderr:
                self.logSignal.emit("maketx errors: " +
                                    result.stderr.decode('utf-8').strip())
            self.logSignal.emit(f"Converted: {texture} -> {arnold_out}")
        except subprocess.CalledProcessError as e:
            self.logSignal.emit(f"Failed to convert {texture} to .tx: {e}")



# -----------------------------------------------------------
# Main UI Class
# -----------------------------------------------------------
class TxConverterUI(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(TxConverterUI, self).__init__(parent)
        self.setWindowTitle("TX Converter v1.0.5")
        self.setGeometry(100, 100, 600, 700)
        self.setMinimumSize(400, 900)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setWindowOpacity(1.0)
        self.setStyleSheet("background-color: #2D2D2D;")
        
        
        # ─── Load settings FIRST ──────────────────────────────
        self.userSettings = self.load_user_settings()

        # ─── Apply any value-overrides to the current process ─
        for var_name, override_val in self.userSettings.get("env_var_overrides", {}).items():
            if override_val:
                os.environ[var_name] = override_val

        self.worker = None
        self.worker_thread = None

        self.COLORS = {
            "background": "#2D2D2D",
            "surface": "#2B2B2B",
            "content_bg": "#363636",
            "primary": "#2196F3",
            "text": "#FFFFFF",
            "secondary_text": "#B0B0B0",
            "input_bg": "#404040",
            "selection": "#3A3A3A",
            "menu_bg": "#363636"
        }
        self.normalOutputStyle = (
            f"background-color: {self.COLORS['input_bg']}; color: {self.COLORS['text']}; "
            "border-radius: 4px; padding: 8px;"
        )
        self.completedOutputStyle = (
            "background-color: #388E3C; color: white; border-radius: 4px; padding: 8px;"
        )

        self.setAcceptDrops(True)
        self.dropped_files = []

        self.resize_margin = 25
        self._is_moving = False
        self._move_start_offset = QtCore.QPoint()

        self.shadow = QtWidgets.QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(20)
        self.shadow.setColor(QtGui.QColor(0, 0, 0, 150))
        self.shadow.setOffset(0, 0)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)

        self.container = QtWidgets.QWidget(self)
        self.container.setStyleSheet(f"background-color: {self.COLORS['background']}; border-radius: 8px;")
        self.container.setGraphicsEffect(self.shadow)
        main_layout.addWidget(self.container)

        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Title Bar
        self.title_bar = QtWidgets.QWidget()
        self.title_bar.setFixedHeight(40)
        self.title_bar.setStyleSheet(
            f"background-color: {self.COLORS['surface']}; border-top-left-radius: 8px; border-top-right-radius: 8px;"
        )
        title_bar_layout = QtWidgets.QHBoxLayout(self.title_bar)
        title_bar_layout.setContentsMargins(16, 0, 8, 0)
        title_bar_layout.setSpacing(12)

        title_label = QtWidgets.QLabel("TX CONVERTER")
        title_label.setStyleSheet(f"color: {self.COLORS['text']}; font-size: 14px; font-weight: bold;")
        title_bar_layout.addWidget(title_label)
        title_bar_layout.addStretch()

        control_style = f"""
            QPushButton {{
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                border: none;
                border-radius: 16px;
                color: {self.COLORS['text']};
            }}
            QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.1); }}
            QPushButton#close:hover {{ background-color: #F44336; }}
        """

        self.minimize_button = QtWidgets.QPushButton("-")
        self.minimize_button.setObjectName("minimize")
        self.minimize_button.setStyleSheet(control_style)
        self.minimize_button.clicked.connect(self.showMinimized)

                # Add "Settings" button in Title Bar (small) to the right of minimize_button
        self.settings_button = QtWidgets.QPushButton("⚙")
        self.settings_button.setObjectName("settings")
        self.settings_button.setStyleSheet(control_style)
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.clicked.connect(self.open_settings_dialog)
        title_bar_layout.addWidget(self.settings_button)  # after minimize_button

        self.close_button = QtWidgets.QPushButton("×")
        self.close_button.setObjectName("close")
        self.close_button.setStyleSheet(control_style)
        self.close_button.clicked.connect(self.close)

        title_bar_layout.addWidget(self.minimize_button)
        title_bar_layout.addWidget(self.close_button)
        self.title_bar.installEventFilter(self)
        container_layout.addWidget(self.title_bar)

        self.content_widget = QtWidgets.QWidget()
        self.content_widget.setStyleSheet(f"background-color: {self.COLORS['content_bg']};")
        content_layout = QtWidgets.QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setSpacing(16)

        folder_label = QtWidgets.QLabel("Select folder to load and group textures:")
        folder_label.setStyleSheet(f"color: {self.COLORS['text']}; font-size: 12px;")
        content_layout.addWidget(folder_label)

        folder_layout = QtWidgets.QHBoxLayout()
        self.folder_line_edit = QtWidgets.QLineEdit()
        self.folder_line_edit.setPlaceholderText("Folder path")
        self.folder_line_edit.setStyleSheet(
            f"background-color: {self.COLORS['input_bg']}; color: {self.COLORS['text']}; border-radius: 4px; padding: 4px;"
        )
        folder_layout.addWidget(self.folder_line_edit)

        choose_folder_btn = QtWidgets.QPushButton("Choose Folder")
        choose_folder_btn.setFixedSize(100, 32)
        choose_folder_btn.setStyleSheet(
            f"background-color: {self.COLORS['primary']}; color: white; border-radius: 16px;"
        )
        choose_folder_btn.clicked.connect(self.choose_folder)
        folder_layout.addWidget(choose_folder_btn)
        content_layout.addLayout(folder_layout)

        self.include_subfolders_checkbox = QtWidgets.QCheckBox("Include Subfolders")
        self.include_subfolders_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.include_subfolders_checkbox.setChecked(True)
        content_layout.addWidget(self.include_subfolders_checkbox)

        load_textures_btn = QtWidgets.QPushButton("Load Textures")
        load_textures_btn.setFixedHeight(36)
        load_textures_btn.setStyleSheet(
            f"background-color: {self.COLORS['primary']}; color: white; border-radius: 18px;"
        )
        load_textures_btn.clicked.connect(self.load_textures)
        content_layout.addWidget(load_textures_btn)

        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.HLine)
        separator.setFrameShadow(QtWidgets.QFrame.Sunken)
        separator.setStyleSheet(f"color: {self.COLORS['surface']};")
        content_layout.addWidget(separator)

        self.compression_checkbox = QtWidgets.QCheckBox("Use Compression")
        self.compression_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.compression_checkbox.setChecked(True)
        content_layout.addWidget(self.compression_checkbox)

        self.add_suffix_checkbox = QtWidgets.QCheckBox("Add missing color space suffix")
        self.add_suffix_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.add_suffix_checkbox.setChecked(False)
        content_layout.addWidget(self.add_suffix_checkbox)

        self.renderman_checkbox = QtWidgets.QCheckBox("Convert to RenderMan .tex")
        self.renderman_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.renderman_checkbox.setChecked(False)
        content_layout.addWidget(self.renderman_checkbox)
        
        # NEW – Houdini .rat checkbox
        self.houdini_rat_checkbox = QtWidgets.QCheckBox("Convert to Houdini .rat")
        self.houdini_rat_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.houdini_rat_checkbox.setChecked(False)
        content_layout.addWidget(self.houdini_rat_checkbox)
        # --------------------------------------

        self.rename_to_acescg_checkbox = QtWidgets.QCheckBox("Rename to ACEScg Color Space")
        self.rename_to_acescg_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.rename_to_acescg_checkbox.setChecked(False)
        content_layout.addWidget(self.rename_to_acescg_checkbox)

        self.renderman_bumprough_checkbox = QtWidgets.QCheckBox("Use Renderman Bump Rough")
        self.renderman_bumprough_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.renderman_bumprough_checkbox.setChecked(False)
        content_layout.addWidget(self.renderman_bumprough_checkbox)

        self.hdri_checkbox = QtWidgets.QCheckBox("HDRI (use 32-bit float for color textures)")
        self.hdri_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.hdri_checkbox.setChecked(False)
        content_layout.addWidget(self.hdri_checkbox)

        tif_label = QtWidgets.QLabel("TIF Color Space:")
        tif_label.setStyleSheet(f"color: {self.COLORS['text']}; font-size: 12px;")
        content_layout.addWidget(tif_label)

        self.tif_srgb_checkbox = QtWidgets.QCheckBox("Treat TIF/TIFF color as sRGB (uncheck for linear)")
        self.tif_srgb_checkbox.setStyleSheet(f"color: {self.COLORS['text']};")
        self.tif_srgb_checkbox.setChecked(True)
        content_layout.addWidget(self.tif_srgb_checkbox)

        separator2 = QtWidgets.QFrame()
        separator2.setFrameShape(QtWidgets.QFrame.HLine)
        separator2.setFrameShadow(QtWidgets.QFrame.Sunken)
        separator2.setStyleSheet(f"color: {self.COLORS['surface']};")
        content_layout.addWidget(separator2)

        self.output_field = QtWidgets.QTextEdit()
        self.output_field.setReadOnly(True)
        self.output_field.setStyleSheet(self.normalOutputStyle)
        self.output_field.setFixedHeight(250)
        content_layout.addWidget(self.output_field)

        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setMinimum(0)
        self.progressBar.setMaximum(0)
        content_layout.addWidget(self.progressBar)

        process_textures_btn = QtWidgets.QPushButton("Process Textures")
        process_textures_btn.setFixedHeight(36)
        process_textures_btn.setStyleSheet(
            f"background-color: {self.COLORS['primary']}; color: white; border-radius: 18px;"
        )
        process_textures_btn.clicked.connect(self.process_textures)
        content_layout.addWidget(process_textures_btn)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.content_widget)
        container_layout.addWidget(self.scroll_area)

        self.log("TX Converter UI initialized.")
        self.log_env_status() 

    def load_user_settings(self):
        """
        Read persistent JSON (if any), merge with defaults,
        and return the result as a dict.
        """
        default = {
            "batch_size": 6,
            "patterns": {
                "raw": "_raw",
                "lin_srgb": "_lin_srgb",
                "acescg": "_acescg",
                "srgb_texture": "_srgb_texture"
            },
            "custom_patterns": {
                "raw": [],
                "lin_srgb": [],
                "acescg": [],
                "srgb_texture": []
            },
            # default logical-role → env-var-NAME mapping
            "env_var_names": {
                "imaketx":  "IMAKETX_PATH",
                "arnold":   "MAKETX_PATH",
                "renderman":"RMANTREE",
                "ocio":     "OCIO",
                "hfs":      "HFS"
            },
            # optional override VALUES keyed by the var-name itself
            "env_var_overrides": {
                "IMAKETX_PATH": "",
                "MAKETX_PATH":  "",
                "RMANTREE":     "",
                "OCIO":         "",
                "HFS":          ""
            }
        }

        settings_path = get_user_settings_path()
        if os.path.isfile(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    user = json.load(f)
            except Exception:
                user = {}
        else:
            user = {}

        # recursive merge
        def merge(dst, src):
            for k, v in src.items():
                if isinstance(v, dict):
                    dst[k] = merge(dst.get(k, {}), v)
                else:
                    dst.setdefault(k, v)
            return dst

        return merge(user, default)


    def save_user_settings(self):
        settings_path = get_user_settings_path()
        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(self.userSettings, f, indent=4)
        except Exception as e:
            self.log(f"Error saving settings: {e}")

    # ------------------ open_settings_dialog ------------------
    def open_settings_dialog(self):
        """Settings dialog: batch size, suffixes, substrings, env-var NAMES."""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.setWindowModality(QtCore.Qt.ApplicationModal)
        dlg.setFixedSize(380, 720)

        lay = QtWidgets.QVBoxLayout(dlg)

        # batch size
        lay.addWidget(QtWidgets.QLabel("Images Converted At Once:"))
        self.batch_spin = QtWidgets.QSpinBox()
        self.batch_spin.setRange(1, 64)
        self.batch_spin.setValue(int(self.userSettings.get("batch_size", 6)))
        lay.addWidget(self.batch_spin)

        # hard suffixes
        lay.addWidget(QtWidgets.QLabel("Single-String Suffix Patterns:"))
        self.raw_line   = QtWidgets.QLineEdit(self.userSettings["patterns"]["raw"])
        self.lin_line   = QtWidgets.QLineEdit(self.userSettings["patterns"]["lin_srgb"])
        self.acg_line   = QtWidgets.QLineEdit(self.userSettings["patterns"]["acescg"])
        self.srgb_line  = QtWidgets.QLineEdit(self.userSettings["patterns"]["srgb_texture"])
        for lbl, w in [("raw", self.raw_line), ("lin_srgb", self.lin_line),
                       ("acescg", self.acg_line), ("srgb_texture", self.srgb_line)]:
            lay.addWidget(QtWidgets.QLabel(f"{lbl}:")); lay.addWidget(w)

        # custom substrings
        lay.addWidget(QtWidgets.QLabel("Custom Name Substrings (comma-separated):"))
        cp = self.userSettings["custom_patterns"]
        def to_str(key): return ", ".join(cp.get(key, []))
        self.raw_cust  = QtWidgets.QLineEdit(to_str("raw"))
        self.lin_cust  = QtWidgets.QLineEdit(to_str("lin_srgb"))
        self.acg_cust  = QtWidgets.QLineEdit(to_str("acescg"))
        self.srgb_cust = QtWidgets.QLineEdit(to_str("srgb_texture"))
        for lbl, w in [("raw", self.raw_cust), ("lin_srgb", self.lin_cust),
                       ("acescg", self.acg_cust), ("srgb_texture", self.srgb_cust)]:
            lay.addWidget(QtWidgets.QLabel(f"{lbl}:")); lay.addWidget(w)

        # env-var NAMES only (no value column)
        lay.addWidget(QtWidgets.QLabel(
            "Environment-Variable Names (edit if your studio uses different ones):",
            styleSheet="font-weight:bold;"))
        roles = ["imaketx", "arnold", "renderman", "ocio", "hfs"]
        names = self.userSettings["env_var_names"]
        self.env_name_edit = {}
        grid = QtWidgets.QGridLayout(); grid.setColumnStretch(1, 1)
        for r, role in enumerate(roles):
            grid.addWidget(QtWidgets.QLabel(role.upper()), r, 0)
            le = QtWidgets.QLineEdit(names.get(role, ""))
            self.env_name_edit[role] = le
            grid.addWidget(le, r, 1)
        lay.addLayout(grid)

        # buttons
        row = QtWidgets.QHBoxLayout()
        ok  = QtWidgets.QPushButton("OK");  ok.clicked.connect(lambda: self.apply_settings(dlg))
        cancel = QtWidgets.QPushButton("Cancel"); cancel.clicked.connect(dlg.reject)
        row.addWidget(ok); row.addWidget(cancel); lay.addLayout(row)

        dlg.exec_()




    def apply_settings(self, dlg):
        """Commit dialog changes, save JSON, refresh log."""
        self.userSettings["batch_size"] = self.batch_spin.value()

        # suffixes
        self.userSettings["patterns"]["raw"]          = self.raw_line.text()
        self.userSettings["patterns"]["lin_srgb"]     = self.lin_line.text()
        self.userSettings["patterns"]["acescg"]       = self.acg_line.text()
        self.userSettings["patterns"]["srgb_texture"] = self.srgb_line.text()

        # custom substrings
        def split(le): return [x.strip() for x in le.text().split(",") if x.strip()]
        self.userSettings["custom_patterns"]["raw"]          = split(self.raw_cust)
        self.userSettings["custom_patterns"]["lin_srgb"]     = split(self.lin_cust)
        self.userSettings["custom_patterns"]["acescg"]       = split(self.acg_cust)
        self.userSettings["custom_patterns"]["srgb_texture"] = split(self.srgb_cust)

        # env-var names (just names, no values)
        roles = ["imaketx", "arnold", "renderman", "ocio", "hfs"]
        new_names = {}
        for role in roles:
            name_txt = self.env_name_edit[role].text().strip()
            if name_txt:
                new_names[role] = name_txt
        self.userSettings["env_var_names"] = new_names

        self.save_user_settings()
        self.log("Settings saved.")
        self.log_env_status()
        dlg.accept()



        
    def log_env_status(self):
        """List each logical role, the resolved env-var name, and whether it is set."""
        roles = ["imaketx", "arnold", "renderman", "ocio", "hfs"]
        names = self.userSettings.get("env_var_names", {})
        self.log("── Environment Variables ──")
        for role in roles:
            var = names.get(role, "<undefined>")
            val = os.environ.get(var)
            dot = "🟢" if val else "🔴"
            display = val if val else "<NOT SET>"
            self.log(f"  {dot} {role.upper():10s} → {var} = {display}")
        self.log("──────────────────────────")


    # ---------- Worker creation changed to pass userSettings ----------

    # DRAG & DROP
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        dropped_paths = []
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                dropped_paths.append(file_path)

        if dropped_paths:
            self.dropped_files = dropped_paths
            self.output_field.setStyleSheet(self.normalOutputStyle)
            self.output_field.clear()
            self.log(f"Dropped {len(dropped_paths)} file(s):")
            for f in dropped_paths:
                self.log("  " + f)
            self.log("When you click 'Process Textures', only dropped files will be processed.")
        event.acceptProposedAction()

    @QtCore.Slot(str)
    def appendLog(self, message):
        current = self.output_field.toHtml()
        safe_msg = message.replace("<", "&lt;").replace(">", "&gt;")
        new_html = current + f"<div>{safe_msg}</div>"
        self.output_field.setHtml(new_html)
        self.output_field.verticalScrollBar().setValue(
            self.output_field.verticalScrollBar().maximum()
        )
        print(message)

    @QtCore.Slot(int)
    def updateProgress(self, value):
        self.progressBar.setValue(value)

    @QtCore.Slot()
    def workerFinished(self):
        self.appendLog("Conversion process completed.")
        self.output_field.setStyleSheet(self.completedOutputStyle)
        self.worker_thread.quit()
        self.worker_thread.wait()
        self.worker = None
        self.worker_thread = None

    def log(self, message):
        self.appendLog(message)

    def choose_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.folder_line_edit.setText(folder)

    def load_textures(self):
        self.dropped_files = []
        self.output_field.setStyleSheet(self.normalOutputStyle)
        self.output_field.clear()

        folder_path = self.folder_line_edit.text().strip()
        if not folder_path:
            QtWidgets.QMessageBox.warning(self, "Warning", "No folder selected.")
            return

        recurse = self.include_subfolders_checkbox.isChecked()
        textures = self.gather_textures(folder_path, recurse)
        if not textures:
            QtWidgets.QMessageBox.warning(self, "Warning", "No valid texture files found in the selected folder.")
            return

        texture_groups = defaultdict(lambda: defaultdict(list))
        tif_srgb = self.tif_srgb_checkbox.isChecked()
        for tex in textures:
            ext = os.path.splitext(tex)[1].lower()
            color_space, additional_options, _ = self.determine_color_space(tex, ext, tif_srgb)
            texture_groups[color_space][ext].append(tex)

        self.display_textures(texture_groups)

    def gather_textures(self, folder_path, recurse=True):
        valid_exts = ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.bmp', '.gif')
        textures = []
        if recurse:
            for root, _, files in os.walk(folder_path):
                for file in files:
                    f_lower = file.lower()
                    if f_lower.endswith(valid_exts) and not f_lower.endswith(('.tex', '.tx')):
                        textures.append(os.path.join(root, file))
        else:
            for file in os.listdir(folder_path):
                f_lower = file.lower()
                if f_lower.endswith(valid_exts) and not f_lower.endswith(('.tex', '.tx')):
                    textures.append(os.path.join(folder_path, file))
        return textures

    def display_textures(self, texture_groups):
        """
        Lists color spaces found, including new 'acescg'.
        """
        self.output_field.clear()
        total_textures = 0
        output = ""
        for color_space, extensions in texture_groups.items():
            output += f"\n{color_space.upper()}:\n"
            for ext, tex_list in extensions.items():
                total_textures += len(tex_list)
                output += f"  {ext.upper()}:\n"
                for tex in tex_list:
                    output += f"    - {os.path.basename(tex)}\n"
                output += "\n"
        output += f"\nTotal Textures to Convert: {total_textures}"
        self.output_field.setPlainText(output.strip())
        self.log(f"Loaded {total_textures} textures.")

    def determine_color_space(self, filename, extension, tif_srgb):
        """
        Determine the color space based on specific suffixes/patterns
        in the base filename, plus extension-based rules if no match.
        Also merges user-defined custom patterns on top of the script's
        existing default detection.
        """

        # 1) Load your user-defined "suffix" patterns (the original single-string patterns):
        p_raw = self.userSettings["patterns"].get("raw", "_raw")
        p_lin_srgb = self.userSettings["patterns"].get("lin_srgb", "_lin_srgb")
        p_acescg = self.userSettings["patterns"].get("acescg", "_acescg")
        p_srgb_texture = self.userSettings["patterns"].get("srgb_texture", "_srgb_texture")

        # 2) Also load any *custom name substrings* the user wants for each color space:
        custom_raw = self.userSettings.get("custom_patterns", {}).get("raw", [])
        custom_lin = self.userSettings.get("custom_patterns", {}).get("lin_srgb", [])
        custom_acescg = self.userSettings.get("custom_patterns", {}).get("acescg", [])
        custom_srgb_tex = self.userSettings.get("custom_patterns", {}).get("srgb_texture", [])

        # We'll combine them with the original suffix so each color space
        # check can pick up either the built-in or the user’s extra terms.
        # (We lower() them during the matching stage below.)
        raw_list = [p_raw.lower()] + [s.lower() for s in custom_raw]
        lin_list = [p_lin_srgb.lower()] + [s.lower() for s in custom_lin]
        acescg_list = [p_acescg.lower()] + [s.lower() for s in custom_acescg]
        srgb_tex_list = [p_srgb_texture.lower()] + [s.lower() for s in custom_srgb_tex]

        base_name = os.path.splitext(os.path.basename(filename))[0]
        base_lower = base_name.lower()

        # -- 1) If any user/built-in “acescg” substring is in the name => color_space = acescg
        if any(sub in base_lower for sub in acescg_list):
            new_name = re.sub(r'(\.[^.]+)$', f"{p_acescg}\\1", filename)
            return 'acescg', '', new_name

        # -- 2) If any user/built-in “raw” substring is in the name => color_space = raw
        if any(sub in base_lower for sub in raw_list):
            new_name = re.sub(r'(\.[^.]+)$', f"{p_raw}\\1", filename)
            return 'raw', '-d float', new_name

        # -- 3) If any user/built-in “srgb_texture” substring is in the name => srgb_texture
        if any(sub in base_lower for sub in srgb_tex_list):
            new_name = re.sub(r'(\.[^.]+)$', f"{p_srgb_texture}\\1", filename)
            return 'srgb_texture', '', new_name

        # -- 4) If any user/built-in “lin_srgb” substring is in the name => lin_srgb
        if any(sub in base_lower for sub in lin_list):
            new_name = re.sub(r'(\.[^.]+)$', f"{p_lin_srgb}\\1", filename)
            return 'lin_srgb', '', new_name

        # -- 5) Next, check your big RAW_DATA_PATTERN for known raw data names
        RAW_DATA_PATTERN = (
            r'_depth|_disp|_displacement|_zdisp|_normal|_nrm|_norm|_n(?![a-z])|_mask'
            r'|_rough|_metal|_gloss|_spec|_ao|_cavity|_bump|_height|_opacity'
            r'|_roughness|_r(?![a-z])|_roughnes|_specularity|_specs|_metalness|_metalnes'
            r'|spcr|bmp|bump|hight|disp|rough|emm|emission|spec|norm|normal'
        )
        if re.search(RAW_DATA_PATTERN, base_lower):
            new_name = re.sub(r'(\.[^.]+)$', f"{p_raw}\\1", filename)
            return 'raw', '-d float', new_name

        # -- 6) If extension == .exr => default to lin_srgb
        if extension == '.exr':
            new_name = re.sub(r'(\.[^.]+)$', f"{p_lin_srgb}\\1", filename)
            return 'lin_srgb', '', new_name

        # -- 7) If extension in TIF => either srgb_texture or lin_srgb based on user checkbox
        if extension in ['.tif', '.tiff']:
            if tif_srgb:
                new_name = re.sub(r'(\.[^.]+)$', f"{p_srgb_texture}\\1", filename)
                return 'srgb_texture', '', new_name
            else:
                new_name = re.sub(r'(\.[^.]+)$', f"{p_lin_srgb}\\1", filename)
                return 'lin_srgb', '', new_name

        # -- 8) Fallback => srgb_texture
        new_name = re.sub(r'(\.[^.]+)$', f"{p_srgb_texture}\\1", filename)
        return 'srgb_texture', '', new_name



    def rename_files(self, folder_path, add_suffix=False, recurse=True):
        renamed_files = []
        skipped_files = []
        valid_exts = ('.exr', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.gif')
        all_files = []

        if recurse:
            for root, _, files in os.walk(folder_path):
                for f in files:
                    all_files.append(os.path.join(root, f))
        else:
            for f in os.listdir(folder_path):
                all_files.append(os.path.join(folder_path, f))

        for file_path in all_files:
            extension = os.path.splitext(file_path)[1].lower()
            if extension not in valid_exts:
                skipped_files.append(file_path)
                continue

            color_space, _, _ = self.determine_color_space(
                file_path, extension, self.tif_srgb_checkbox.isChecked()
            )
            # If color_space is one of [raw, srgb_texture, lin_srgb, acescg], we rename if needed
            if color_space not in ['raw', 'srgb_texture', 'lin_srgb', 'acescg']:
                skipped_files.append(file_path)
                continue

            if add_suffix:
                base, ext = os.path.splitext(os.path.basename(file_path))
                # Now includes '_acescg'
                suffixes = ['_raw', '_srgb_texture', '_lin_srgb', '_acescg']
                if not any(suf in base.lower() for suf in suffixes):
                    new_file_name = f"{base}_{color_space}{ext}"
                    new_path = os.path.join(os.path.dirname(file_path), new_file_name)
                    try:
                        os.rename(file_path, new_path)
                        renamed_files.append((file_path, new_path))
                    except Exception as e:
                        self.log(f"Error renaming {file_path}: {e}")
                else:
                    skipped_files.append(file_path)
            else:
                skipped_files.append(file_path)

        self.log(f"Renamed {len(renamed_files)} files.")
        for old, new in renamed_files:
            self.log(f"  {old} -> {new}")
        self.log(f"Skipped {len(skipped_files)} files.")
        return renamed_files

    def rename_dropped_files(self, file_list):
        renamed_files = []
        skipped_files = []
        updated_paths = []
        valid_exts = ('.exr', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.gif')

        for file_path in file_list:
            extension = os.path.splitext(file_path)[1].lower()
            if extension not in valid_exts:
                skipped_files.append(file_path)
                updated_paths.append(file_path)
                continue

            color_space, _, _ = self.determine_color_space(
                file_path, extension, self.tif_srgb_checkbox.isChecked()
            )
            if color_space not in ['raw', 'srgb_texture', 'lin_srgb', 'acescg']:
                skipped_files.append(file_path)
                updated_paths.append(file_path)
                continue

            base, ext = os.path.splitext(os.path.basename(file_path))
            # includes `_acescg`
            if any(suf in base.lower() for suf in ['_raw','_srgb_texture','_lin_srgb','_acescg']):
                skipped_files.append(file_path)
                updated_paths.append(file_path)
            else:
                new_file_name = f"{base}_{color_space}{ext}"
                new_path = os.path.join(os.path.dirname(file_path), new_file_name)
                try:
                    os.rename(file_path, new_path)
                    renamed_files.append((file_path, new_path))
                    updated_paths.append(new_path)
                except Exception as e:
                    self.log(f"Error renaming {file_path}: {e}")
                    updated_paths.append(file_path)

        self.log(f"Renamed {len(renamed_files)} dropped files.")
        for old, new in renamed_files:
            self.log(f"  {old} -> {new}")
        self.log(f"Skipped {len(skipped_files)} dropped files.")
        return updated_paths

    def process_textures(self):
        if self.dropped_files:
            self.log("Processing dropped file(s) only...")
            if self.add_suffix_checkbox.isChecked():
                self.log("Adding missing color space suffixes to dropped file(s)...")
                self.dropped_files = self.rename_dropped_files(self.dropped_files)
            textures = self.dropped_files
        else:
            folder_path = self.folder_line_edit.text().strip()
            if not folder_path:
                QtWidgets.QMessageBox.warning(self, "Warning", "No folder path found.")
                return

            add_suffix_selected = self.add_suffix_checkbox.isChecked()
            recurse = self.include_subfolders_checkbox.isChecked()

            if add_suffix_selected:
                self.log("Adding missing color space suffixes...")
                self.rename_files(folder_path, add_suffix=add_suffix_selected, recurse=recurse)

            textures = self.gather_textures(folder_path, recurse=recurse)

        self.log(f"Total textures found: {len(textures)}")

        selected_textures = []
        skipped_textures = []
        tif_srgb = self.tif_srgb_checkbox.isChecked()

        for tex in textures:
            extension = os.path.splitext(tex)[1].lower()
            color_space, additional_options, _ = self.determine_color_space(tex, extension, tif_srgb)
            if color_space in ["lin_srgb", "srgb_texture", "raw", "acescg"]:
                selected_textures.append((tex, color_space, additional_options))
            else:
                skipped_textures.append(tex)

        if skipped_textures:
            self.log(f"Skipped textures (unrecognized color space): {len(skipped_textures)}")
            for st in skipped_textures:
                self.log("  " + st)

        total = len(selected_textures)
        if total == 0:
            QtWidgets.QMessageBox.warning(
                self, "Warning",
                "No textures matched the recognized color spaces for processing."
            )
            return

        self.progressBar.setMaximum(total)
        self.progressBar.setValue(0)
        self.log(f"Starting conversion of {total} textures...")

        rename_to_acescg = self.rename_to_acescg_checkbox.isChecked()
        use_compression = self.compression_checkbox.isChecked()
        use_renderman = self.renderman_checkbox.isChecked()
        hdri_mode = self.hdri_checkbox.isChecked()
        use_renderman_bumprough = self.renderman_bumprough_checkbox.isChecked()
        
        use_houdini_rat = self.houdini_rat_checkbox.isChecked()  # NEW

        self.worker_thread = QtCore.QThread()
        self.worker = TextureWorker(
            selected_textures,
            rename_to_acescg,
            self.add_suffix_checkbox.isChecked(),
            use_compression,
            use_renderman,
            hdri_mode=hdri_mode,
            use_renderman_bumprough=use_renderman_bumprough,
            userSettings=self.userSettings, #NEW: pass the loaded settings
            use_houdini_rat=use_houdini_rat           # NEW
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker.logSignal.connect(self.appendLog)
        self.worker.progressSignal.connect(self.updateProgress)
        self.worker.finishedSignal.connect(self.workerFinished)
        self.worker_thread.started.connect(self.worker.run)
        self.worker_thread.start()

    def eventFilter(self, obj, event):
        if obj == self.title_bar:
            if event.type() == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.LeftButton:
                    self._is_moving = True
                    self._move_start_offset = event.globalPos() - self.frameGeometry().topLeft()
                    return True
            elif event.type() == QtCore.QEvent.MouseMove:
                if self._is_moving:
                    self.move(event.globalPos() - self._move_start_offset)
                    return True
            elif event.type() == QtCore.QEvent.MouseButtonRelease:
                if event.button() == QtCore.Qt.LeftButton:
                    self._is_moving = False
                    return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            pos = event.pos()
            if pos.x() >= self.width() - self.resize_margin or pos.y() >= self.height() - self.resize_margin:
                self._is_resizing = True
                self._resize_start_pos = event.globalPos()
                self._resize_start_geo = self.geometry()
                event.accept()
            else:
                event.ignore()

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            if getattr(self, "_is_resizing", False):
                delta = event.globalPos() - self._resize_start_pos
                new_geo = QtCore.QRect(self._resize_start_geo)
                new_geo.setWidth(max(self.minimumWidth(), self._resize_start_geo.width() + delta.x()))
                new_geo.setHeight(max(self.minimumHeight(), self._resize_start_geo.height() + delta.y()))
                self.setGeometry(new_geo)
                event.accept()
            else:
                self.update_resize_cursor(event.pos())
                event.ignore()
        else:
            self.update_resize_cursor(event.pos())

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            setattr(self, "_is_resizing", False)
            self.unsetCursor()
        event.accept()

    def update_resize_cursor(self, pos):
        if (self.width() - pos.x()) <= self.resize_margin and (self.height() - pos.y()) <= self.resize_margin:
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        else:
            self.unsetCursor()


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = TxConverterUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

# txconverter 2.0 end#
