import maya.cmds as cmds
import os
import re
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor


def create_ui():
    global folder_path_field, srgb_checkbox, lin_srgb_checkbox, raw_checkbox, texture_output_field, compression_checkbox, add_suffix_checkbox

    if cmds.window("txConverter", exists=True):
        cmds.deleteUI("txConverter")

    window = cmds.window("txConverter", title="Convert to .tx", widthHeight=(400, 500))
    cmds.columnLayout(adjustableColumn=True)

    # Path Selection
    cmds.text(label="Select folder to load and group textures:")
    folder_path_field = cmds.textField(editable=True)
    cmds.button(label="Choose Folder", command=lambda _: cmds.textField(folder_path_field, edit=True, text=cmds.fileDialog2(fileMode=3)[0]))
    cmds.button(label="Load Textures", command=lambda _: load_textures(cmds.textField(folder_path_field, query=True, text=True)))

    # Texture Type Checkboxes
    cmds.text(label="Select texture type:")
    srgb_checkbox = None
    lin_srgb_checkbox = None
    raw_checkbox = None

    # Compression Flag Checkbox
    compression_checkbox = cmds.checkBox(label="Use DWA Compression", value=True)

    # Add Suffix Checkbox
    add_suffix_checkbox = cmds.checkBox(label="Add missing color space suffix", value=False)

    # Output Text Area
    texture_output_field = cmds.scrollField(editable=False, wordWrap=True, height=200)

    cmds.button(label="Process Textures", command=lambda _: process_selected_textures())
    cmds.showWindow(window)


def rename_files(folder_path, add_suffix=False):
    renamed_files = []
    skipped_files = []

    for root, _, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            extension = os.path.splitext(file)[1].lower()
            if extension not in ['.exr', '.jpg', '.png', '.tif', '.tiff', '.bmp', '.gif']:
                skipped_files.append(file_path)
                continue

            # Determine color space
            color_space, _, new_name = determine_color_space(file, extension)
            if not color_space:
                skipped_files.append(file_path)
                continue

            # Check if suffix is missing
            if add_suffix and color_space not in file.lower():
                base_name, ext = os.path.splitext(file)
                renamed_file = f"{base_name}_{color_space}{ext}"
                renamed_path = os.path.join(root, renamed_file)
                os.rename(file_path, renamed_path)
                renamed_files.append((file_path, renamed_path))
            else:
                skipped_files.append(file_path)

    # Debug Output
    print(f"Renamed {len(renamed_files)} files:")
    for old, new in renamed_files:
        print(f"  {old} -> {new}")
    print(f"Skipped {len(skipped_files)} files:")
    for skipped in skipped_files:
        print(f"  {skipped}")

    return renamed_files


def load_textures(folder_path):
    if not folder_path:
        cmds.warning("No folder selected.")
        return

    # Collect textures from the folder
    textures = [os.path.join(root, file) for root, _, files in os.walk(folder_path) for file in files
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.bmp', '.gif'))]

    if not textures:
        cmds.warning("No texture files found in the selected folder.")
        return

    # Group textures by color space and extension
    texture_groups = group_textures_by_color_space_and_extension(textures)

    # Display grouped textures and add checkboxes dynamically if .tif is found
    display_textures(texture_groups)
    add_checkboxes_if_tif_found(texture_groups)


def group_textures_by_color_space_and_extension(textures):
    texture_groups = defaultdict(lambda: defaultdict(list))

    for texture in textures:
        ext = os.path.splitext(texture)[1].lower()
        color_space, _, _ = determine_color_space(texture, ext)

        texture_groups[color_space][ext].append(texture)

    return texture_groups


def determine_color_space(filename, extension):
    """
    Determine the color space of a texture based on its filename patterns and extension.
    """
    # Extended RAW textures (non-color data)
    if re.search(
        r'_depth|_disp|_normal|_mask|_rough|_metal|_gloss|_spec|_ao|_cavity|_bump|_displacement|_nrm|_height|_zdisp|_norm|_n|_opacity|_roughness|_r|_roughnes|_specularity|_specs|_b|_metalness|_metalnes',
        filename,
        re.IGNORECASE
    ):
        return 'raw', '-d float', filename.replace(extension, '_raw' + extension)

    # Color textures (albedo, diffuse, etc.)
    if re.search(r'_albedo|_basecolor|_color|_colour|_diff|_dif|_diffuse', filename, re.IGNORECASE):
        if extension.lower() in ['.jpg', '.png', '.jpeg', '.bmp', '.gif', '.tif']:
            return 'srgb_texture', '', filename.replace(extension, '_srgb_texture' + extension)
        elif extension.lower() == '.exr':
            return 'lin_srgb', '', filename.replace(extension, '_lin_srgb' + extension)

    # Ignore non-image files
    if extension.lower() not in ['.jpg', '.png', '.jpeg', '.bmp', '.gif', '.tif', '.tiff', '.exr']:
        print(f"Skipped non-image file: {filename}")
        return 'unknown', '', filename

    # Default handling for extensions
    if extension.lower() == '.exr':
        return 'lin_srgb', '', filename  # Default .exr to lin_srgb
    elif extension.lower() in ['.png', '.jpg', '.jpeg', '.bmp', '.gif']:
        return 'srgb_texture', '', filename  # Default other image formats to srgb_texture
    elif extension.lower() in ['.tif', '.tiff']:
        return 'unknown', '', filename  # Unknown for .tif to allow manual selection

    # Unknown textures
    return 'unknown', '', filename


def display_textures(texture_groups):
    cmds.scrollField(texture_output_field, edit=True, clear=True)

    total_textures = 0
    output = ""
    for color_space, extensions in texture_groups.items():
        output += f"\n{color_space.upper()}:\n"
        for ext, textures in extensions.items():
            total_textures += len(textures)
            output += f"  {ext.upper()}:\n"
            output += "\n".join(f"    - {os.path.basename(tex)}" for tex in textures)
            output += "\n"

    output += f"\nTotal Textures to Convert: {total_textures}"
    cmds.scrollField(texture_output_field, edit=True, text=output.strip())


def add_checkboxes_if_tif_found(texture_groups):
    global srgb_checkbox

    if 'srgb_texture' in texture_groups and '.tif' in texture_groups['srgb_texture']:
        print("TIF found in SRGB_TEXTURE group. Adding checkbox.")
        if srgb_checkbox is None:
            srgb_checkbox = cmds.checkBox(label="sRGB Texture (TIF)", value=False)
    else:
        print("No TIF found in SRGB_TEXTURE group. Checkboxes will not be shown.")


def process_selected_textures():
    folder_path = cmds.textField(folder_path_field, query=True, text=True)
    if not folder_path:
        cmds.warning("No folder path found.")
        return

    # Handle renaming if checkbox is selected
    add_suffix_selected = cmds.checkBox(add_suffix_checkbox, query=True, value=True)
    if add_suffix_selected:
        print("Adding missing color space suffixes...")
        rename_files(folder_path, add_suffix=add_suffix_selected)

    # Proceed with processing textures
    textures = [os.path.join(root, file) for root, _, files in os.walk(folder_path) for file in files]
    print(f"Total textures found: {len(textures)}")

    # Filter by selected types
    selected_textures = []
    skipped_textures = []
    for texture in textures:
        color_space, additional_options, _ = determine_color_space(texture, os.path.splitext(texture)[1].lower())
        if color_space in ["lin_srgb", "srgb_texture", "raw"]:
            selected_textures.append((texture, color_space, additional_options))
        else:
            skipped_textures.append(texture)

    # Debug: Log skipped textures
    if skipped_textures:
        print(f"Skipped textures: {len(skipped_textures)}")
        for tex in skipped_textures:
            print(f"Skipped: {tex}")

    # Start conversion process
    if selected_textures:
        print(f"Converting {len(selected_textures)} textures...")
        with ThreadPoolExecutor(max_workers=8) as executor:
            executor.map(lambda args: convert_texture_to_tx(*args), selected_textures)
    else:
        cmds.warning("No textures matched the selected types for processing.")


def convert_texture_to_tx(texture, color_space, additional_options):
    arnold_path = os.environ.get("MAKETX_PATH", "maketx")
    color_config = os.environ.get("OCIO_CONFIG", "")
    output_folder = os.path.dirname(texture)
    base_name = os.path.splitext(os.path.basename(texture))[0]
    ext = os.path.splitext(texture)[1].lower()[1:]

    duplicate_files = [file for file in os.listdir(output_folder) if base_name in file and file.endswith(('.exr', '.jpg', '.png', '.tif', '.tiff', '.bmp', '.gif'))]
    if len(duplicate_files) > 1 and ext != 'exr':
        tx_name = f"{base_name}_{ext}.tx"
    else:
        tx_name = f"{base_name}.tx"

    output_path = os.path.join(output_folder, tx_name)

    if not arnold_path or not os.path.exists(arnold_path):
        cmds.warning("maketx path not set or invalid. Please check your environment variables.")
        return

    is_displacement = re.search(r'_disp|_displacement|_zdisp', texture, re.IGNORECASE)

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

    if is_displacement:
        print(f"Skipping compression for displacement file: {texture}")
    else:
        print(f"Applying compression to file: {texture}")

    command = [arnold_path, '-v', '-o', output_path, '-u', '--format', 'exr', '-d', bit_depth] + compression_flag + ['--oiio', texture]

    if color_space in ['lin_srgb', 'srgb_texture', 'raw'] and color_config:
        command += ['--colorconfig', color_config]
        if color_space == 'lin_srgb':
            command += ['--colorconvert', 'lin_srgb', 'ACES - ACEScg']
        elif color_space == 'srgb_texture':
            command += ['--colorconvert', 'srgb_texture', 'ACES - ACEScg']

    print("Constructed maketx command:")
    print(' '.join(command))

    try:
        subprocess.run(command, check=True)
        print(f"Converted: {texture} -> {output_path}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {texture}: {e}")


create_ui()
