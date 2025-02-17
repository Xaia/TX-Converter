import os
import re
import sys
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6 import QtCore, QtGui, QtWidgets
try:
    from shiboken6 import wrapInstance
except ImportError:
    from shiboken import wrapInstance

# -----------------------------------------------------------
# (Removed host-specific main window functions)
# -----------------------------------------------------------
# In a standalone app, we don't need to get a host main window.
# Instead, our dialog will run independently.

# -----------------------------------------------------------
# Worker Class for Texture Conversion
# -----------------------------------------------------------
class TextureWorker(QtCore.QObject):
    progressSignal = QtCore.Signal(int)  # emits the number of textures processed so far
    logSignal = QtCore.Signal(str)       # emits log messages
    finishedSignal = QtCore.Signal()     # emitted when all conversions are done

    def __init__(self, textures, rename_to_acescg, add_suffix_selected, use_compression, use_renderman, parent=None):
        super(TextureWorker, self).__init__(parent)
        self.textures = textures
        self.rename_to_acescg = rename_to_acescg
        self.add_suffix_selected = add_suffix_selected
        self.use_compression = use_compression
        self.use_renderman = use_renderman

    def run(self):
        total = len(self.textures)
        batch_size = 6
        processed = 0

        for i in range(0, total, batch_size):
            batch = self.textures[i:i+batch_size]
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                futures = {executor.submit(self.convert_texture, texture, color_space, additional_options):
                           (texture, color_space) for texture, color_space, additional_options in batch}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self.logSignal.emit("Error during conversion: {}".format(e))
                    processed += 1
                    self.progressSignal.emit(processed)
        self.finishedSignal.emit()

    def convert_texture(self, texture, color_space, additional_options):
        self.logSignal.emit("Starting conversion for {}...".format(os.path.basename(texture)))
        arnold_path = os.environ.get("MAKETX_PATH", "maketx")
        color_config = os.environ.get("OCIO", "")
        renderman_path = os.environ.get("RMANTREE", "")
        txmake_path = os.path.join(renderman_path, "bin", "txmake") if renderman_path else None

        output_folder = os.path.dirname(texture)
        base_name = os.path.splitext(os.path.basename(texture))[0]
        ext = os.path.splitext(texture)[1].lower()[1:]
        if ext in ["tex", "tx"]:
            self.logSignal.emit("Skipping already-processed file: {}".format(texture))
            return

        if self.rename_to_acescg:
            base_name = re.sub(r'(_raw|_srgb_texture|_lin_srgb)$', '', base_name, flags=re.IGNORECASE)
            suffix = "_acescg"
        else:
            if self.add_suffix_selected:
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
        if self.use_compression and not is_displacement:
            compression_flag = ['--compression', 'dwaa']

        # RenderMan conversion branch.
        if self.use_renderman and txmake_path:
            self.logSignal.emit("Converting {} to RenderMan .tex...".format(os.path.basename(texture)))
            txmake_command = [txmake_path]
            if bit_depth in ['half', 'float']:
                txmake_command += ["-mode", "luminance"]
            txmake_command += [texture, renderman_output_path]
            try:
                result = subprocess.run(txmake_command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.logSignal.emit("txmake output: " + result.stdout.decode('utf-8').strip())
                if result.stderr:
                    self.logSignal.emit("txmake errors: " + result.stderr.decode('utf-8').strip())
                self.logSignal.emit("Converted to .tex: {} -> {}".format(texture, renderman_output_path))
            except subprocess.CalledProcessError as e:
                self.logSignal.emit("Failed to convert {} to .tex: {}".format(texture, e))
            return
        # Otherwise, perform Arnold conversion using maketx.
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

        self.logSignal.emit("Converting {} to Arnold .tx...".format(os.path.basename(texture)))
        try:
            result = subprocess.run(command, shell=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.logSignal.emit("maketx output: " + result.stdout.decode('utf-8').strip())
            if result.stderr:
                self.logSignal.emit("maketx errors: " + result.stderr.decode('utf-8').strip())
            self.logSignal.emit("Converted: {} -> {}".format(texture, arnold_output_path))
        except subprocess.CalledProcessError as e:
            self.logSignal.emit("Failed to convert {} to .tx: {}".format(texture, e))


# -----------------------------------------------------------
# Main UI Class
# -----------------------------------------------------------
class TxConverterUI(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(TxConverterUI, self).__init__(parent)
        self.setWindowTitle("TX Converter")
        self.setGeometry(100, 100, 600, 700)
        self.setMinimumSize(400, 850)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setWindowOpacity(1.0)
        self.setStyleSheet("background-color: #2D2D2D;")  # Opaque background

        # Placeholders for worker thread and worker.
        self.worker = None
        self.worker_thread = None

        # Define colors
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
        # Styles for the output info window.
        self.normalOutputStyle = "background-color: {}; color: {}; border-radius: 4px; padding: 8px;".format(
            self.COLORS["input_bg"], self.COLORS["text"])
        self.completedOutputStyle = "background-color: #388E3C; color: white; border-radius: 4px; padding: 8px;"

        # Variables for moving/resizing the window
        self.resize_margin = 25
        self._is_moving = False
        self._move_start_offset = QtCore.QPoint()

        # Drop shadow effect for container
        self.shadow = QtWidgets.QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(20)
        self.shadow.setColor(QtGui.QColor(0, 0, 0, 150))
        self.shadow.setOffset(0, 0)

        # Main Layout & Container
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)
        self.setStyleSheet("background-color: {};".format(self.COLORS["background"]))

        self.container = QtWidgets.QWidget(self)
        self.container.setStyleSheet("background-color: {}; border-radius: 8px;".format(self.COLORS["background"]))
        self.container.setGraphicsEffect(self.shadow)
        main_layout.addWidget(self.container)

        container_layout = QtWidgets.QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Custom Title Bar
        self.title_bar = QtWidgets.QWidget()
        self.title_bar.setFixedHeight(40)
        self.title_bar.setStyleSheet("background-color: {}; border-top-left-radius: 8px; border-top-right-radius: 8px;".format(self.COLORS["surface"]))
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
        self.title_bar.installEventFilter(self)
        container_layout.addWidget(self.title_bar)

        # Scrollable Content Area
        self.content_widget = QtWidgets.QWidget()
        self.content_widget.setStyleSheet("background-color: {};".format(self.COLORS["content_bg"]))
        content_layout = QtWidgets.QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setSpacing(16)

        # --- load_textures() method (restores output style to normal) ---
        def load_textures_impl():
            # When new textures are loaded, revert the output field style to normal.
            self.output_field.setStyleSheet(self.normalOutputStyle)
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
        self.load_textures = load_textures_impl
        # --- end load_textures() ---

        # Folder Selection
        folder_label = QtWidgets.QLabel("Select folder to load and group textures:")
        folder_label.setStyleSheet("color: {}; font-size: 12px;".format(self.COLORS["text"]))
        content_layout.addWidget(folder_label)

        folder_layout = QtWidgets.QHBoxLayout()
        self.folder_line_edit = QtWidgets.QLineEdit()
        self.folder_line_edit.setPlaceholderText("Folder path")
        self.folder_line_edit.setStyleSheet("background-color: {}; color: {}; border-radius: 4px; padding: 4px;".format(
            self.COLORS["input_bg"], self.COLORS["text"]))
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
        self.output_field.setStyleSheet(self.normalOutputStyle)
        self.output_field.setFixedHeight(250)
        content_layout.addWidget(self.output_field)

        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setMinimum(0)
        self.progressBar.setMaximum(0)  # will be set when conversion starts
        content_layout.addWidget(self.progressBar)

        process_textures_btn = QtWidgets.QPushButton("Process Textures")
        process_textures_btn.setFixedHeight(36)
        process_textures_btn.setStyleSheet("background-color: {}; color: white; border-radius: 18px;".format(self.COLORS["primary"]))
        process_textures_btn.clicked.connect(self.process_textures)
        content_layout.addWidget(process_textures_btn)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.content_widget)
        container_layout.addWidget(self.scroll_area)

        self.log("TX Converter UI initialized.")

    @QtCore.Slot(str)
    def appendLog(self, message):
        current = self.output_field.toPlainText()
        new_text = current + message + "\n"
        self.output_field.setPlainText(new_text)
        self.output_field.verticalScrollBar().setValue(self.output_field.verticalScrollBar().maximum())
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

    def log(self, message):
        self.appendLog(message)

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

        total = len(selected_textures)
        if total == 0:
            QtWidgets.QMessageBox.warning(self, "Warning", "No textures matched the recognized color spaces for processing.")
            return

        self.progressBar.setMaximum(total)
        self.progressBar.setValue(0)
        self.log("Starting conversion of {} textures...".format(total))

        rename_to_acescg = self.rename_to_acescg_checkbox.isChecked()
        use_compression = self.compression_checkbox.isChecked()
        use_renderman = self.renderman_checkbox.isChecked()

        # Set up the worker thread.
        self.worker_thread = QtCore.QThread()
        self.worker = TextureWorker(selected_textures, rename_to_acescg, add_suffix_selected, use_compression, use_renderman)
        self.worker.moveToThread(self.worker_thread)
        self.worker.logSignal.connect(self.appendLog)
        self.worker.progressSignal.connect(self.updateProgress)
        self.worker.finishedSignal.connect(self.workerFinished)
        self.worker_thread.started.connect(self.worker.run)
        self.worker_thread.start()

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
            if self._is_resizing:
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
            self._is_resizing = False
            self.unsetCursor()
        event.accept()

    def update_resize_cursor(self, pos):
        x, y = pos.x(), pos.y()
        if (self.width() - x) <= self.resize_margin and (self.height() - y) <= self.resize_margin:
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        else:
            self.unsetCursor()

# -----------------------------------------------------------
# Main Function for Standalone Execution
# -----------------------------------------------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    window = TxConverterUI()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
