import maya.cmds as cmds
import os
import re
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor


def create_ui():
    global folder_path_field, srgb_checkbox, lin_srgb_checkbox, raw_checkbox, texture_output_field

    if cmds.window("txConverter", exists=True):
        cmds.deleteUI("txConverter")

    window = cmds.window("txConverter", title="Convert to .tx", widthHeight=(400, 400))
    cmds.columnLayout(adjustableColumn=True)

    # Path Selection
    cmds.text(label="Select folder to load and group textures:")
    folder_path_field = cmds.textField(editable=True)
    cmds.button(label="Choose Folder", command=lambda _: cmds.textField(folder_path_field, edit=True, text=cmds.fileDialog2(fileMode=3)[0]))
    cmds.button(label="Load Textures", command=lambda _: load_textures(cmds.textField(folder_path_field, query=True, text=True)))

    # Texture Type Checkboxes
    cmds.text(label="Select texture type:")
    srgb_checkbox = cmds.checkBox(label="sRGB Texture", value=False)
    lin_srgb_checkbox = cmds.checkBox(label="Linear sRGB (lin_srgb)", value=False)
    raw_checkbox = cmds.checkBox(label="Raw", value=False)

    # Output Text Area
    texture_output_field = cmds.scrollField(editable=False, wordWrap=True, height=200)

    cmds.button(label="Process Textures", command=lambda _: process_selected_textures())
    cmds.showWindow(window)



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

    # Display grouped textures
    display_textures(texture_groups)


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
    Args:
        filename (str): The name of the texture file (e.g., "example_8K_Albedo.exr").
        extension (str): The file extension (e.g., ".exr", ".png").
    Returns:
        tuple: (color_space, additional_options, new_filename)
            - color_space: The detected color space (e.g., "raw", "lin_srgb", "srgb_texture").
            - additional_options: Any additional options needed for processing.
            - new_filename: Suggested new filename with the appropriate suffix.
    """

    # RAW textures (non-color data)
    if re.search(r'_depth|_disp|_normal|_mask|_rough|_metal|_gloss|_spec|_ao|_cavity|_bump|_displacement|_nrm', filename, re.IGNORECASE):
        return 'raw', '-d float', filename.replace(extension, '_raw' + extension)

    # Color textures (albedo, diffuse, etc.)
    if re.search(r'_albedo|_basecolor|_color|_colour|_diff|_dif|_diffuse', filename, re.IGNORECASE):
        if extension.lower() in ['.jpg', '.png', '.jpeg', '.bmp', '.gif', '.tif']:
            return 'srgb_texture', '', filename.replace(extension, '_srgb_texture' + extension)
        elif extension.lower() == '.exr':
            return 'lin_srgb', '', filename.replace(extension, '_lin_srgb' + extension)

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

    output = ""
    for color_space, extensions in texture_groups.items():
        output += f"\n{color_space.upper()}:\n"
        for ext, textures in extensions.items():
            output += f"  {ext.upper()}:\n"
            output += "\n".join(f"    - {os.path.basename(tex)}" for tex in textures)
            output += "\n"

    cmds.scrollField(texture_output_field, edit=True, text=output.strip())

    # Auto-select checkboxes based on groups
    cmds.checkBox(srgb_checkbox, edit=True, value="srgb_texture" in texture_groups)
    cmds.checkBox(lin_srgb_checkbox, edit=True, value="lin_srgb" in texture_groups)
    cmds.checkBox(raw_checkbox, edit=True, value="raw" in texture_groups)


def process_selected_textures():
    srgb_selected = cmds.checkBox(srgb_checkbox, query=True, value=True)
    lin_srgb_selected = cmds.checkBox(lin_srgb_checkbox, query=True, value=True)
    raw_selected = cmds.checkBox(raw_checkbox, query=True, value=True)

    if not (srgb_selected or lin_srgb_selected or raw_selected):
        cmds.warning("No texture type selected. Please select at least one type.")
        return

    folder_path = cmds.textField(folder_path_field, query=True, text=True)  # Corrected reference
    if not folder_path:
        cmds.warning("No folder path found.")
        return

    # Collect textures for conversion
    textures = [os.path.join(root, file) for root, _, files in os.walk(folder_path) for file in files]

    # Filter by selected types
    selected_textures = []
    for texture in textures:
        color_space, additional_options, _ = determine_color_space(texture, os.path.splitext(texture)[1].lower())
        if (srgb_selected and color_space == "srgb_texture") or \
           (lin_srgb_selected and color_space == "lin_srgb") or \
           (raw_selected and color_space == "raw"):
            selected_textures.append((texture, color_space, additional_options))

    # Start conversion process
    if selected_textures:
        with ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(lambda args: convert_texture_to_tx(*args), selected_textures)
    else:
        cmds.warning("No textures matched the selected types for processing.")



def convert_texture_to_tx(texture, color_space, additional_options):
    arnold_path = os.environ.get("MAKETX_PATH", "maketx")  # Use environment variable for maketx
    color_config = os.environ.get("OCIO_CONFIG", "")  # Use environment variable for OCIO config
    output_color_space = "ACES - ACEScg"
    output_folder = os.path.dirname(texture)
    output_path = os.path.join(output_folder, f"{os.path.splitext(os.path.basename(texture))[0]}.tx")

    if not arnold_path or not os.path.exists(arnold_path):
        cmds.warning("maketx path not set or invalid. Please check your environment variables.")
        return

    if 'raw' in color_space.lower():
        command = [arnold_path, '-v', '-o', output_path, '-u', '--format', 'exr', '--fixnan', 'box3', '--oiio', texture]
    elif 'lin_srgb' in color_space.lower():
        command = [
            arnold_path, '-v', '-o', output_path, '-u', '--format', 'exr', additional_options, '--fixnan', 'box3',
            '--colorconfig', color_config, '--colorconvert', 'lin_srgb', output_color_space, '--unpremult', '--oiio', texture
        ]
    elif 'srgb_texture' in color_space.lower():
        command = [
            arnold_path, '-v', '-o', output_path, '-u', '--format', 'exr', additional_options, '--fixnan', 'box3',
            '--colorconfig', color_config, '--colorconvert', 'srgb_texture', output_color_space, '--unpremult', '--oiio', texture
        ]

    try:
        subprocess.run(command, check=True)
        print(f"Converted: {texture} -> {output_path}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {texture}: {e}")


create_ui()
