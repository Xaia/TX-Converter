import maya.cmds as cmds
import os
import re
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

# A pattern of “non-color” (raw) texture types, with `_b` removed
RAW_DATA_PATTERN = (
    r'_depth|_disp|_displacement|_zdisp|_normal|_nrm|_norm|_n(?![a-z])|_mask|_rough|_metal'
    r'|_gloss|_spec|_ao|_cavity|_bump|_height|_opacity|_roughness|_r(?![a-z])|_roughnes'
    r'|_specularity|_specs|_metalness|_metalnes'
)

def create_ui():
    """
    Builds the UI for folder selection, options, and the main conversion button.
    """
    global folder_path_field
    global texture_output_field
    global compression_checkbox
    global add_suffix_checkbox
    global renderman_checkbox
    global rename_to_acescg_checkbox
    global tif_srgb_checkbox
    global include_subfolders_checkbox

    if cmds.window("txConverter", exists=True):
        cmds.deleteUI("txConverter")

    window = cmds.window("txConverter", title="Convert to .tx/.tex", widthHeight=(400, 650))
    cmds.columnLayout(adjustableColumn=True)

    # Path Selection
    cmds.text(label="Select folder to load and group textures:")
    folder_path_field = cmds.textField(editable=True)
    cmds.button(
        label="Choose Folder",
        command=lambda _: cmds.textField(
            folder_path_field, edit=True, text=cmds.fileDialog2(fileMode=3)[0]
        )
    )

    # Give the checkBox a proper UI name for subfolders
    include_subfolders_checkbox = cmds.checkBox(
        "include_subfolders_checkbox",
        label="Include Subfolders",
        value=True
    )

    cmds.separator(h=5, style="none")
    cmds.button(
        label="Load Textures",
        command=lambda _: load_textures(
            cmds.textField(folder_path_field, query=True, text=True)
        )
    )

    cmds.separator(h=10, style="in")

    # Compression Flag Checkbox
    compression_checkbox = cmds.checkBox(label="Use DWA Compression", value=True)

    # Add Suffix Checkbox
    add_suffix_checkbox = cmds.checkBox(label="Add missing color space suffix", value=False)

    # RenderMan Checkbox
    renderman_checkbox = cmds.checkBox(label="Convert to RenderMan .tex", value=False)

    # Rename to ACEScg Checkbox
    rename_to_acescg_checkbox = cmds.checkBox(label="Rename to ACEScg Color Space", value=False)

    cmds.separator(h=10, style="none")

    cmds.text(label="TIF Color Space:")
    tif_srgb_checkbox = cmds.checkBox(
        "tif_srgb_checkbox",
        label="Treat TIF/TIFF color as sRGB (uncheck for linear)",
        value=True
    )

    cmds.separator(h=10, style="none")

    # Output Text Area
    texture_output_field = cmds.scrollField(editable=False, wordWrap=True, height=250)

    cmds.separator(h=5, style="none")
    cmds.button(label="Process Textures", command=lambda _: process_selected_textures())

    cmds.showWindow(window)


def gather_textures(folder_path, recurse=True):
    """
    Gathers all valid image files from the specified folder.
    If recurse=False, only processes the top-level directory; otherwise uses os.walk.
    Excludes .tex/.tx from the results.
    """
    valid_exts = ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.bmp', '.gif')
    textures = []

    if recurse:
        # Full recursion with os.walk
        for root, _, files in os.walk(folder_path):
            for file in files:
                f_lower = file.lower()
                if f_lower.endswith(valid_exts) and not f_lower.endswith(('.tex', '.tx')):
                    textures.append(os.path.join(root, file))
    else:
        # Only the top-level folder
        for file in os.listdir(folder_path):
            f_lower = file.lower()
            if f_lower.endswith(valid_exts) and not f_lower.endswith(('.tex', '.tx')):
                textures.append(os.path.join(folder_path, file))

    return textures


def load_textures(folder_path):
    """
    Gathers valid image files and groups them by color space.
    Displays them in the scrollField area. Honors the "Include Subfolders" checkbox.
    """
    if not folder_path:
        cmds.warning("No folder selected.")
        return

    # Query the actual UI name we gave it: "include_subfolders_checkbox"
    recurse = cmds.checkBox("include_subfolders_checkbox", query=True, value=True)

    # Collect textures
    textures = gather_textures(folder_path, recurse=recurse)
    if not textures:
        cmds.warning("No valid texture files found in the selected folder.")
        return

    # Group by color space
    texture_groups = defaultdict(lambda: defaultdict(list))
    for tex in textures:
        ext = os.path.splitext(tex)[1].lower()
        color_space, _, _ = determine_color_space(tex, ext)
        texture_groups[color_space][ext].append(tex)

    # Display grouped textures
    display_textures(texture_groups)


def display_textures(texture_groups):
    """
    Writes out the grouped textures (by color space & extension) in the scrollField UI.
    """
    cmds.scrollField(texture_output_field, edit=True, clear=True)

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
    cmds.scrollField(texture_output_field, edit=True, text=output.strip())


def determine_color_space(filename, extension):
    """
    Priority:
      1) If the name already has _raw, _srgb_texture, or _lin_srgb => use that
      2) Else if matches data pattern => raw
      3) Else if .exr => lin_srgb
      4) Else if .tif => TIF checkbox
      5) Else => srgb_texture
    """
    base_name = os.path.splitext(os.path.basename(filename))[0]
    base_lower = base_name.lower()

    # 1) If name explicitly has a color space
    if "_raw" in base_lower:
        new_name = re.sub(r'(\.[^.]+)$', '_raw\\1', filename)
        return 'raw', '-d float', new_name
    elif "_srgb_texture" in base_lower:
        new_name = re.sub(r'(\.[^.]+)$', '_srgb_texture\\1', filename)
        return 'srgb_texture', '', new_name
    elif "_lin_srgb" in base_lower:
        new_name = re.sub(r'(\.[^.]+)$', '_lin_srgb\\1', filename)
        return 'lin_srgb', '', new_name

    # 2) If file name indicates data
    if re.search(RAW_DATA_PATTERN, base_lower):
        new_name = re.sub(r'(\.[^.]+)$', '_raw\\1', filename)
        return 'raw', '-d float', new_name

    # 3) .exr => lin_srgb
    if extension == '.exr':
        new_name = re.sub(r'(\.[^.]+)$', '_lin_srgb\\1', filename)
        return 'lin_srgb', '', new_name

    # 4) .tif => check the "tif_srgb_checkbox"
    elif extension in ['.tif', '.tiff']:
        is_tif_srgb = cmds.checkBox("tif_srgb_checkbox", query=True, value=True)
        if is_tif_srgb:
            new_name = re.sub(r'(\.[^.]+)$', '_srgb_texture\\1', filename)
            return 'srgb_texture', '', new_name
        else:
            new_name = re.sub(r'(\.[^.]+)$', '_lin_srgb\\1', filename)
            return 'lin_srgb', '', new_name

    # 5) Otherwise => srgb_texture
    new_name = re.sub(r'(\.[^.]+)$', '_srgb_texture\\1', filename)
    return 'srgb_texture', '', new_name


def rename_files(folder_path, add_suffix=False, recurse=True):
    """
    Optionally appends the _raw / _srgb_texture / _lin_srgb suffix if not present.
    Honors the "Include Subfolders" setting as well.
    """
    renamed_files = []
    skipped_files = []

    # Gather files
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

        color_space, _, new_name = determine_color_space(file_path, extension)
        if color_space not in ['raw', 'srgb_texture', 'lin_srgb']:
            skipped_files.append(file_path)
            continue

        if add_suffix:
            base, ext = os.path.splitext(os.path.basename(file_path))
            suffixes = ['_raw', '_srgb_texture', '_lin_srgb']
            if not any(suf in base.lower() for suf in suffixes):
                new_file_name = base + f"_{color_space}" + ext
                new_path = os.path.join(os.path.dirname(file_path), new_file_name)
                os.rename(file_path, new_path)
                renamed_files.append((file_path, new_path))
            else:
                skipped_files.append(file_path)
        else:
            skipped_files.append(file_path)

    # Debug output
    print(f"Renamed {len(renamed_files)} files:")
    for old, new in renamed_files:
        print(f"  {old} -> {new}")
    print(f"Skipped {len(skipped_files)} files:")
    for skipped in skipped_files:
        print(f"  {skipped}")

    return renamed_files


def process_selected_textures():
    """
    After user clicks "Process Textures":
      1) optional rename
      2) gather recognized images
      3) convert
    """
    folder_path = cmds.textField(folder_path_field, query=True, text=True)
    if not folder_path:
        cmds.warning("No folder path found.")
        return

    # Check if we rename with color space suffix
    add_suffix_selected = cmds.checkBox(add_suffix_checkbox, query=True, value=True)

    # Check subfolder checkbox
    recurse = cmds.checkBox("include_subfolders_checkbox", query=True, value=True)

    if add_suffix_selected:
        print("Adding missing color space suffixes...")
        rename_files(folder_path, add_suffix=add_suffix_selected, recurse=recurse)

    # Gather recognized images
    textures = gather_textures(folder_path, recurse=recurse)
    print(f"Total textures found: {len(textures)}")

    selected_textures = []
    skipped_textures = []
    for tex in textures:
        extension = os.path.splitext(tex)[1].lower()
        color_space, additional_options, _ = determine_color_space(tex, extension)

        if color_space in ["lin_srgb", "srgb_texture", "raw"]:
            selected_textures.append((tex, color_space, additional_options))
        else:
            skipped_textures.append(tex)

    if skipped_textures:
        print(f"Skipped textures (unrecognized color space): {len(skipped_textures)}")
        for st in skipped_textures:
            print(f"  {st}")

    # Convert recognized
    if selected_textures:
        print(f"Converting {len(selected_textures)} textures...")
        with ThreadPoolExecutor(max_workers=8) as executor:
            executor.map(lambda args: convert_texture_to_tx(*args), selected_textures)
    else:
        cmds.warning("No textures matched the recognized color spaces for processing.")


def convert_texture_to_tx(texture, color_space, additional_options):
    """
    Convert a texture to .tx (Arnold) or .tex (RenderMan).
    """
    arnold_path = os.environ.get("MAKETX_PATH", "maketx")
    color_config = os.environ.get("RV_OCIO", "")
    renderman_path = os.environ.get("RMANTREE", "")
    txmake_path = os.path.join(renderman_path, "bin", "txmake") if renderman_path else None

    output_folder = os.path.dirname(texture)
    base_name = os.path.splitext(os.path.basename(texture))[0]
    ext = os.path.splitext(texture)[1].lower()[1:]

    if ext in ["tex", "tx"]:
        print(f"Skipping already-processed file: {texture}")
        return

    # ACEScg rename?
    rename_to_acescg = cmds.checkBox(rename_to_acescg_checkbox, query=True, value=True)
    if rename_to_acescg:
        base_name = re.sub(r'(_raw|_srgb_texture|_lin_srgb)$', '', base_name, flags=re.IGNORECASE)
        suffix = "_acescg"
    else:
        # Check if the add suffix checkbox is enabled
        add_suffix_selected = cmds.checkBox(add_suffix_checkbox, query=True, value=True)
        if add_suffix_selected:
            # Only add suffix if not already present
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

    use_compression = cmds.checkBox(compression_checkbox, query=True, value=True)
    compression_flag = []
    if use_compression and not is_displacement:
        compression_flag = ['--compression', 'dwaa']

    # RenderMan .tex?
    use_renderman = cmds.checkBox(renderman_checkbox, query=True, value=True)
    if use_renderman and txmake_path:
        print(f"Converting {texture} to RenderMan .tex...")
        txmake_command = [txmake_path, texture, renderman_output_path]
        if bit_depth in ['half', 'float']:
            txmake_command += ["-mode", "luminance"]

        print("Constructed txmake command:")
        print(' '.join(txmake_command))

        try:
            subprocess.run(txmake_command, check=True)
            print(f"Converted to .tex: {texture} -> {renderman_output_path}")
        except subprocess.CalledProcessError as e:
            print(f"Failed to convert {texture} to .tex: {e}")
        return

    # Otherwise, Arnold .tx
    print(f"Converting {texture} to Arnold .tx...")
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
        # raw => no colorconvert

    print("Constructed maketx command:")
    print(' '.join(command))

    try:
        subprocess.run(command, check=True)
        print(f"Converted: {texture} -> {arnold_output_path}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {texture} to .tx: {e}")


create_ui()
