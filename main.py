"""
This script creates a modern, frameless PySide2 dialog inside Autodesk Maya
that implements your TX conversion tool. It preserves all conversion logic
and options while using a sleek UI with a drop shadow and rounded corners.
This version avoids translucent top-level backgrounds (which can cause
"window-in-window" artifacts in Maya) by using a fully opaque window.
"""

import os
import re
import sys
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import maya.cmds as cmds
import maya.OpenMayaUI as omui

from PySide2 import QtCore, QtGui, QtWidgets
try:
    from shiboken2 import wrapInstance
except ImportError:
    from shiboken import wrapInstance

# -----------------------------------------------------------
# Helper to get Maya’s main window (to parent our dialog)
# -----------------------------------------------------------
def get_maya_main_window():
    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is not None:
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    return None

# -----------------------------------------------------------
# TX Converter UI (Modern, Frameless, Maya-Integrated)
# -----------------------------------------------------------
class TxConverterUI(QtWidgets.QDialog):
    def __init__(self, parent=get_maya_main_window()):
        super(TxConverterUI, self).__init__(parent)
        self.setWindowTitle("TX Converter")
        self.setGeometry(100, 100, 600, 700)
        self.setMinimumSize(400, 850)
        # Use a frameless window but do NOT set a translucent attribute
        # so that the entire dialog is opaque.
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setWindowOpacity(1.0)
        
        # Define colors for our UI
        self.COLORS = {
            "background": "#2D2D2D",  # used for container and window background
            "surface": "#2B2B2B",     # title bar background
            "content_bg": "#363636",
            "primary": "#2196F3",
            "text": "#FFFFFF",
            "secondary_text": "#B0B0B0",
            "input_bg": "#404040",
            "selection": "#3A3A3A",
            "menu_bg": "#363636"
        }
        
        # Variables for moving/resizing the window
        self.resize_margin = 25
        self._is_moving = False
        self._is_resizing = False
        self._resize_edge = None
        self._move_start_offset = QtCore.QPoint()
        self._resize_start_pos = QtCore.QPoint()
        self._resize_start_geo = QtCore.QRect()
        
        # Create a drop shadow effect for the container
        self.shadow = QtWidgets.QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(20)
        self.shadow.setColor(QtGui.QColor(0, 0, 0, 150))
        self.shadow.setOffset(0, 0)
        
        # ---------------------------
        # Main Layout & Container
        # ---------------------------
        main_layout = QtWidgets.QVBoxLayout(self)
        # Set a margin so the drop shadow can be visible
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)
        # Set the dialog's background color (opaque) to match the container
        self.setStyleSheet("background-color: {};".format(self.COLORS["background"]))
        
        # The container is our custom UI area (with rounded corners and a drop shadow)
        self.container = QtWidgets.QWidget(self)
        self.container.setStyleSheet(
            "background-color: {}; border-radius: 8px;".format(self.COLORS["background"])
        )
        self.container.setGraphicsEffect(self.shadow)
        main_layout.addWidget(self.container)
        
        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # ---------------------------
        # Custom Title Bar
        # ---------------------------
        self.title_bar = QtWidgets.QWidget()
        self.title_bar.setFixedHeight(40)
        self.title_bar.setStyleSheet(
            "background-color: {}; border-top-left-radius: 8px; border-top-right-radius: 8px;".format(self.COLORS["surface"])
        )
        title_bar_layout = QtWidgets.QHBoxLayout(self.title_bar)
        title_bar_layout.setContentsMargins(16, 0, 8, 0)
        title_bar_layout.setSpacing(12)
        
        title_label = QtWidgets.QLabel("TX CONVERTER")
        title_label.setStyleSheet("color: {}; font-size: 14px; font-weight: bold;".format(self.COLORS["text"]))
        title_bar_layout.addWidget(title_label)
        title_bar_layout.addStretch()
        
        control_style = """
            QPushButton {
                min-width: 32px;
                max-width: 32px;
                min-height: 32px;
                max-height: 32px;
                border: none;
                border-radius: 16px;
                color: %s;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); }
            QPushButton#close:hover { background-color: #F44336; }
        """ % self.COLORS["text"]
        
        self.minimize_button = QtWidgets.QPushButton("-")
        self.minimize_button.setObjectName("minimize")
        self.minimize_button.setStyleSheet(control_style)
        self.minimize_button.clicked.connect(self.showMinimized)
        
        self.close_button = QtWidgets.QPushButton("×")
        self.close_button.setObjectName("close")
        self.close_button.setStyleSheet(control_style)
        self.close_button.clicked.connect(self.close)
        
        title_bar_layout.addWidget(self.minimize_button)
        title_bar_layout.addWidget(self.close_button)
        # Install an event filter on the title bar so that dragging it moves the window.
        self.title_bar.installEventFilter(self)
        container_layout.addWidget(self.title_bar)
        
        # ---------------------------
        # Scrollable Content Area
        # ---------------------------
        self.content_widget = QtWidgets.QWidget()
        self.content_widget.setStyleSheet("background-color: {};".format(self.COLORS["content_bg"]))
        content_layout = QtWidgets.QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setSpacing(16)
        
        # Folder Selection
        folder_label = QtWidgets.QLabel("Select folder to load and group textures:")
        folder_label.setStyleSheet("color: {}; font-size: 12px;".format(self.COLORS["text"]))
        content_layout.addWidget(folder_label)
        
        folder_layout = QtWidgets.QHBoxLayout()
        self.folder_line_edit = QtWidgets.QLineEdit()
        self.folder_line_edit.setPlaceholderText("Folder path")
        self.folder_line_edit.setStyleSheet(
            "background-color: {}; color: {}; border-radius: 4px; padding: 4px;".format(
                self.COLORS["input_bg"], self.COLORS["text"]
            )
        )
        folder_layout.addWidget(self.folder_line_edit)
        
        choose_folder_btn = QtWidgets.QPushButton("Choose Folder")
        choose_folder_btn.setFixedSize(100, 32)
        choose_folder_btn.setStyleSheet("background-color: {}; color: white; border-radius: 16px;".format(self.COLORS["primary"]))
        choose_folder_btn.clicked.connect(self.choose_folder)
        folder_layout.addWidget(choose_folder_btn)
        content_layout.addLayout(folder_layout)
        
        # Include Subfolders Checkbox
        self.include_subfolders_checkbox = QtWidgets.QCheckBox("Include Subfolders")
        self.include_subfolders_checkbox.setStyleSheet("color: {};".format(self.COLORS["text"]))
        self.include_subfolders_checkbox.setChecked(True)
        content_layout.addWidget(self.include_subfolders_checkbox)
        
        # Load Textures Button
        load_textures_btn = QtWidgets.QPushButton("Load Textures")
        load_textures_btn.setFixedHeight(36)
        load_textures_btn.setStyleSheet("background-color: {}; color: white; border-radius: 18px;".format(self.COLORS["primary"]))
        load_textures_btn.clicked.connect(self.load_textures)
        content_layout.addWidget(load_textures_btn)
        
        # Separator
        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.HLine)
        separator.setFrameShadow(QtWidgets.QFrame.Sunken)
        separator.setStyleSheet("color: {};".format(self.COLORS["surface"]))
        content_layout.addWidget(separator)
        
        # Options Checkboxes
        self.compression_checkbox = QtWidgets.QCheckBox("Use DWA Compression")
        self.compression_checkbox.setStyleSheet("color: {};".format(self.COLORS["text"]))
        self.compression_checkbox.setChecked(True)
        content_layout.addWidget(self.compression_checkbox)
        
        self.add_suffix_checkbox = QtWidgets.QCheckBox("Add missing color space suffix")
        self.add_suffix_checkbox.setStyleSheet("color: {};".format(self.COLORS["text"]))
        self.add_suffix_checkbox.setChecked(False)
        content_layout.addWidget(self.add_suffix_checkbox)
        
        self.renderman_checkbox = QtWidgets.QCheckBox("Convert to RenderMan .tex")
        self.renderman_checkbox.setStyleSheet("color: {};".format(self.COLORS["text"]))
        self.renderman_checkbox.setChecked(False)
        content_layout.addWidget(self.renderman_checkbox)
        
        self.rename_to_acescg_checkbox = QtWidgets.QCheckBox("Rename to ACEScg Color Space")
        self.rename_to_acescg_checkbox.setStyleSheet("color: {};".format(self.COLORS["text"]))
        self.rename_to_acescg_checkbox.setChecked(False)
        content_layout.addWidget(self.rename_to_acescg_checkbox)
        
        tif_label = QtWidgets.QLabel("TIF Color Space:")
        tif_label.setStyleSheet("color: {}; font-size: 12px;".format(self.COLORS["text"]))
        content_layout.addWidget(tif_label)
        
        self.tif_srgb_checkbox = QtWidgets.QCheckBox("Treat TIF/TIFF color as sRGB (uncheck for linear)")
        self.tif_srgb_checkbox.setStyleSheet("color: {};".format(self.COLORS["text"]))
        self.tif_srgb_checkbox.setChecked(True)
        content_layout.addWidget(self.tif_srgb_checkbox)
        
        separator2 = QtWidgets.QFrame()
        separator2.setFrameShape(QtWidgets.QFrame.HLine)
        separator2.setFrameShadow(QtWidgets.QFrame.Sunken)
        separator2.setStyleSheet("color: {};".format(self.COLORS["surface"]))
        content_layout.addWidget(separator2)
        
        self.output_field = QtWidgets.QTextEdit()
        self.output_field.setReadOnly(True)
        self.output_field.setStyleSheet(
            "background-color: {}; color: {}; border-radius: 4px; padding: 8px;".format(
                self.COLORS["input_bg"], self.COLORS["text"]
            )
        )
        self.output_field.setFixedHeight(250)
        content_layout.addWidget(self.output_field)
        
        process_textures_btn = QtWidgets.QPushButton("Process Textures")
        process_textures_btn.setFixedHeight(36)
        process_textures_btn.setStyleSheet("background-color: {}; color: white; border-radius: 18px;".format(self.COLORS["primary"]))
        process_textures_btn.clicked.connect(self.process_textures)
        content_layout.addWidget(process_textures_btn)
        
        # Place the content inside a scroll area for full accessibility
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.content_widget)
        container_layout.addWidget(self.scroll_area)
        
        self.log("TX Converter UI initialized.")
        
    # ---------------------------
    # Event Filter for Title Bar (for window moving)
    # ---------------------------
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
        return super(TxConverterUI, self).eventFilter(obj, event)
        
    # ---------------------------
    # Logging Helper
    # ---------------------------
    def log(self, message):
        current_text = self.output_field.toPlainText()
        new_text = current_text + message + "\n"
        self.output_field.setPlainText(new_text)
        self.output_field.verticalScrollBar().setValue(self.output_field.verticalScrollBar().maximum())
        print(message)
        
    # ---------------------------
    # UI Callbacks
    # ---------------------------
    def choose_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.folder_line_edit.setText(folder)
            
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
        
    def load_textures(self):
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
        
    def display_textures(self, texture_groups):
        self.output_field.clear()
        total_textures = 0
        output = ""
        for color_space, extensions in texture_groups.items():
            output += "\n{}:\n".format(color_space.upper())
            for ext, tex_list in extensions.items():
                total_textures += len(tex_list)
                output += "  {}:\n".format(ext.upper())
                for tex in tex_list:
                    output += "    - {}\n".format(os.path.basename(tex))
                output += "\n"
        output += "\nTotal Textures to Convert: {}".format(total_textures)
        self.output_field.setPlainText(output.strip())
        self.log("Loaded {} textures.".format(total_textures))
        
    def determine_color_space(self, filename, extension, tif_srgb):
        base_name = os.path.splitext(os.path.basename(filename))[0]
        base_lower = base_name.lower()
        
        if "_raw" in base_lower:
            new_name = re.sub(r'(\.[^.]+)$', '_raw\\1', filename)
            return 'raw', '-d float', new_name
        elif "_srgb_texture" in base_lower:
            new_name = re.sub(r'(\.[^.]+)$', '_srgb_texture\\1', filename)
            return 'srgb_texture', '', new_name
        elif "_lin_srgb" in base_lower:
            new_name = re.sub(r'(\.[^.]+)$', '_lin_srgb\\1', filename)
            return 'lin_srgb', '', new_name
            
        RAW_DATA_PATTERN = (
            r'_depth|_disp|_displacement|_zdisp|_normal|_nrm|_norm|_n(?![a-z])|_mask|_rough|_metal'
            r'|_gloss|_spec|_ao|_cavity|_bump|_height|_opacity|_roughness|_r(?![a-z])|_roughnes'
            r'|_specularity|_specs|_metalness|_metalnes'
        )
        if re.search(RAW_DATA_PATTERN, base_lower):
            new_name = re.sub(r'(\.[^.]+)$', '_raw\\1', filename)
            return 'raw', '-d float', new_name
            
        if extension == '.exr':
            new_name = re.sub(r'(\.[^.]+)$', '_lin_srgb\\1', filename)
            return 'lin_srgb', '', new_name
        elif extension in ['.tif', '.tiff']:
            if tif_srgb:
                new_name = re.sub(r'(\.[^.]+)$', '_srgb_texture\\1', filename)
                return 'srgb_texture', '', new_name
            else:
                new_name = re.sub(r'(\.[^.]+)$', '_lin_srgb\\1', filename)
                return 'lin_srgb', '', new_name
                
        new_name = re.sub(r'(\.[^.]+)$', '_srgb_texture\\1', filename)
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
                
            color_space, _, new_name = self.determine_color_space(file_path, extension, self.tif_srgb_checkbox.isChecked())
            if color_space not in ['raw', 'srgb_texture', 'lin_srgb']:
                skipped_files.append(file_path)
                continue
                
            if add_suffix:
                base, ext = os.path.splitext(os.path.basename(file_path))
                suffixes = ['_raw', '_srgb_texture', '_lin_srgb']
                if not any(suf in base.lower() for suf in suffixes):
                    new_file_name = base + f"_{color_space}" + ext
                    new_path = os.path.join(os.path.dirname(file_path), new_file_name)
                    try:
                        os.rename(file_path, new_path)
                        renamed_files.append((file_path, new_path))
                    except Exception as e:
                        self.log("Error renaming {}: {}".format(file_path, e))
                else:
                    skipped_files.append(file_path)
            else:
                skipped_files.append(file_path)
                
        self.log("Renamed {} files.".format(len(renamed_files)))
        for old, new in renamed_files:
            self.log("  {} -> {}".format(old, new))
        self.log("Skipped {} files.".format(len(skipped_files)))
        return renamed_files
        
    def process_textures(self):
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
        self.log("Total textures found: {}".format(len(textures)))
        
        selected_textures = []
        skipped_textures = []
        tif_srgb = self.tif_srgb_checkbox.isChecked()
        
        for tex in textures:
            extension = os.path.splitext(tex)[1].lower()
            color_space, additional_options, _ = self.determine_color_space(tex, extension, tif_srgb)
            if color_space in ["lin_srgb", "srgb_texture", "raw"]:
                selected_textures.append((tex, color_space, additional_options))
            else:
                skipped_textures.append(tex)
                
        if skipped_textures:
            self.log("Skipped textures (unrecognized color space): {}".format(len(skipped_textures)))
            for st in skipped_textures:
                self.log("  " + st)
                
        if selected_textures:
            self.log("Converting {} textures...".format(len(selected_textures)))
            rename_to_acescg = self.rename_to_acescg_checkbox.isChecked()
            use_compression = self.compression_checkbox.isChecked()
            use_renderman = self.renderman_checkbox.isChecked()
            
            def worker(args):
                texture, color_space, additional_options = args
                self.convert_texture_to_tx(texture, color_space, additional_options,
                                             rename_to_acescg, add_suffix_selected,
                                             use_compression, use_renderman)
            with ThreadPoolExecutor(max_workers=8) as executor:
                executor.map(worker, selected_textures)
                
            self.log("Conversion process completed.")
        else:
            QtWidgets.QMessageBox.warning(self, "Warning", "No textures matched the recognized color spaces for processing.")
            
    def convert_texture_to_tx(self, texture, color_space, additional_options,
                              rename_to_acescg, add_suffix_selected,
                              use_compression, use_renderman):
        arnold_path = os.environ.get("MAKETX_PATH", "maketx")
        color_config = os.environ.get("RV_OCIO", "")
        renderman_path = os.environ.get("RMANTREE", "")
        txmake_path = os.path.join(renderman_path, "bin", "txmake") if renderman_path else None
        
        output_folder = os.path.dirname(texture)
        base_name = os.path.splitext(os.path.basename(texture))[0]
        ext = os.path.splitext(texture)[1].lower()[1:]
        if ext in ["tex", "tx"]:
            self.log("Skipping already-processed file: {}".format(texture))
            return
            
        if rename_to_acescg:
            base_name = re.sub(r'(_raw|_srgb_texture|_lin_srgb)$', '', base_name, flags=re.IGNORECASE)
            suffix = "_acescg"
        else:
            if add_suffix_selected:
                if not re.search(r'(_raw|_srgb_texture|_lin_srgb)$', base_name, re.IGNORECASE):
                    suffix = f"_{color_space}"
                else:
                    suffix = ""
            else:
                suffix = ""
                
        arnold_output_path = os.path.join(output_folder, f"{base_name}{suffix}.tx")
        renderman_output_path = os.path.join(output_folder, f"{base_name}{suffix}.tex")
        
        is_displacement = re.search(r'_disp|_displacement|_zdisp', base_name, re.IGNORECASE)
        if is_displacement:
            bit_depth = 'float'
        elif ext in ['jpg', 'jpeg', 'gif', 'bmp']:
            bit_depth = 'uint8'
        elif ext in ['png', 'tif', 'tiff', 'exr']:
            bit_depth = 'half'
        else:
            bit_depth = 'uint16'
            
        compression_flag = []
        if use_compression and not is_displacement:
            compression_flag = ['--compression', 'dwaa']
            
        if use_renderman and txmake_path:
            self.log("Converting {} to RenderMan .tex...".format(texture))
            txmake_command = [txmake_path, texture, renderman_output_path]
            if bit_depth in ['half', 'float']:
                txmake_command += ["-mode", "luminance"]
            self.log("Constructed txmake command: " + ' '.join(txmake_command))
            try:
                subprocess.run(txmake_command, check=True)
                self.log("Converted to .tex: {} -> {}".format(texture, renderman_output_path))
            except subprocess.CalledProcessError as e:
                self.log("Failed to convert {} to .tex: {}".format(texture, e))
            return
            
        self.log("Converting {} to Arnold .tx...".format(texture))
        command = [
            arnold_path,
            '-v',
            '-o', arnold_output_path,
            '-u',
            '--format', 'exr',
            '-d', bit_depth
        ] + compression_flag + ['--oiio', texture]
        
        if color_space in ['lin_srgb', 'srgb_texture', 'raw'] and color_config:
            command += ['--colorconfig', color_config]
            if color_space == 'lin_srgb':
                command += ['--colorconvert', 'lin_srgb', 'ACES - ACEScg']
            elif color_space == 'srgb_texture':
                command += ['--colorconvert', 'srgb_texture', 'ACES - ACEScg']
                
        self.log("Constructed maketx command: " + ' '.join(command))
        try:
            subprocess.run(command, check=True)
            self.log("Converted: {} -> {}".format(texture, arnold_output_path))
        except subprocess.CalledProcessError as e:
            self.log("Failed to convert {} to .tx: {}".format(texture, e))
            
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            pos = event.pos()
            on_right = (pos.x() >= self.width() - self.resize_margin)
            on_bottom = (pos.y() >= self.height() - self.resize_margin)
            if on_right or on_bottom:
                self._is_resizing = True
                self._resize_start_pos = event.globalPos()
                self._resize_start_geo = self.geometry()
                if on_right and on_bottom:
                    self._resize_edge = 'right_bottom'
                elif on_right:
                    self._resize_edge = 'right'
                else:
                    self._resize_edge = 'bottom'
                event.accept()
            else:
                event.ignore()
                
    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            if self._is_resizing:
                delta = event.globalPos() - self._resize_start_pos
                new_geo = QtCore.QRect(self._resize_start_geo)
                if 'right' in self._resize_edge:
                    new_geo.setWidth(max(self.minimumWidth(), self._resize_start_geo.width() + delta.x()))
                if 'bottom' in self._resize_edge:
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
            self._is_resizing = False
            self._resize_edge = None
            self.unsetCursor()
        event.accept()
                
    def update_resize_cursor(self, pos):
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        near_right = (w - x) <= self.resize_margin
        near_bottom = (h - y) <= self.resize_margin
        if near_right and near_bottom:
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        elif near_right:
            self.setCursor(QtCore.Qt.SizeHorCursor)
        elif near_bottom:
            self.setCursor(QtCore.Qt.SizeVerCursor)
        else:
            self.unsetCursor()
            
def show_tx_converter_standalone():
    global tx_converter_dialog
    try:
        parent = get_maya_main_window()
    except Exception as e:
        parent = None
        print("Could not obtain Maya main window: {}".format(e))
    if cmds.window("TXConverterWindow", exists=True):
        cmds.deleteUI("TXConverterWindow", window=True)
    tx_converter_dialog = TxConverterUI(parent)
    tx_converter_dialog.setObjectName("TXConverterWindow")
    tx_converter_dialog.show()
    tx_converter_dialog.raise_()
    tx_converter_dialog.activateWindow()
    
if __name__ == "__main__":
    show_tx_converter_standalone()
