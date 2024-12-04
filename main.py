import maya.cmds as cmds
import os
import subprocess
import re
from concurrent.futures import ThreadPoolExecutor

def create_ui():
    if cmds.window("txConverter", exists=True):
        cmds.deleteUI("txConverter")

    window = cmds.window("txConverter", title="Convert to .tx", widthHeight=(300, 200))
    cmds.columnLayout(adjustableColumn=True)
    
    cmds.text(label="Select folder to convert images:")
    folder_path_field = cmds.textField(editable=True)
    
    cmds.button(label="Choose Folder", command=lambda _: cmds.textField(folder_path_field, edit=True, text=cmds.fileDialog2(fileMode=3)[0]))
    cmds.button(label="Convert Images", command=lambda _: process_folder(cmds.textField(folder_path_field, query=True, text=True)))
    
    cmds.showWindow(window)

def process_folder(folder_path):
    if not folder_path:
        cmds.warning("No folder selected.")
        return
    
    image_files = [os.path.join(root, file) for root, _, files in os.walk(folder_path) for file in files 
                    if file.lower().endswith(('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.exr', '.bmp', '.gif'))]
    
    if not image_files:
        cmds.warning("No image files found in the selected folder or its subfolders.")
        return

    print(f"Found {len(image_files)} images to convert.")
    with ThreadPoolExecutor(max_workers=4) as executor:  # Adjust max_workers as needed
        executor.map(convert_image, image_files, [folder_path]*len(image_files))

def convert_image(img_path, base_folder):
    ARNOLD_PATH = r"D:/ConfigurationSync/inPipeline/software/windows/maya_addons/2024/mtoa/5.3.2.0/bin/maketx.exe"
    COLOR_CONFIG = r"D:/ConfigurationSync/ColorManagement/ocio/aces_1.0.3/config.ocio"
    OUTPUT_COLOR_SPACE = "ACES - ACEScg"
    
    filename = os.path.basename(img_path)
    base_name, ext = os.path.splitext(filename)
    out_dir = os.path.join(base_folder, os.path.relpath(os.path.dirname(img_path), base_folder))
    os.makedirs(out_dir, exist_ok=True)
    output_path = os.path.join(out_dir, f"{base_name}.tx")

    color_space, additional_options, new_filename = determine_color_space(filename, ext)

    if not color_space:
        print(f"Could not determine color space for {filename}")
        return

    if 'raw' in color_space.lower():  
        command = [
            ARNOLD_PATH, '-v', '-o', output_path, '-u', '--format', 'exr', 
            '--fixnan', 'box3', '--oiio', img_path  # Skip colorconfig and colorconvert
        ]
    elif 'lin_srgb' in color_space.lower(): 
        command = [
            ARNOLD_PATH, '-v', '-o', output_path, '-u', '--format', 'exr', 
            additional_options, '--fixnan', 'box3', '--colorconfig', COLOR_CONFIG, 
            '--colorconvert', 'lin_srgb', OUTPUT_COLOR_SPACE, '--unpremult', '--oiio', img_path
        ]
    elif 'srgb_texture' in color_space.lower(): 
        command = [
            ARNOLD_PATH, '-v', '-o', output_path, '-u', '--format', 'exr', 
            additional_options, '--fixnan', 'box3', '--colorconfig', COLOR_CONFIG, 
            '--colorconvert', 'srgb_texture', OUTPUT_COLOR_SPACE, '--unpremult', '--oiio', img_path
        ]
    else:
        command = [
            ARNOLD_PATH, '-v', '-o', output_path, '-u', '--format', 'exr', 
            additional_options, '--fixnan', 'box3', '--colorconfig', COLOR_CONFIG, 
            '--colorconvert', color_space, OUTPUT_COLOR_SPACE, '--unpremult', '--oiio', img_path
        ]
    
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Successfully converted {filename}")
        if result.stdout:
            print("Output:", result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Failed to convert {filename}: {e}\nCommand: {' '.join(command)}")
        print(f"Error Output: {e.stderr}")
def determine_color_space(filename, extension):
    patterns = {
        '_depth|_disp|_normal|_mask|_glossiness|_gloss|_opacity|_translucency|_height|_rough|_roughness|_metal|_displacement|_nrm': ('raw', '-d float', filename.replace(extension, '_raw' + extension)),
        '_diff|_color|_dif|_albedo|_baseColor|_srgb_texture': ('srgb_texture', '', filename.replace(extension, '_srgb_texture' + extension)),
        '_baseColor': ('lin_srgb' if extension.lower() == '.exr' else 'srgb_texture', '', filename.replace(extension, '_lin_srgb' + extension) if extension.lower() == '.exr' else filename.replace(extension, '_srgb_texture' + extension)),
        '_lin_srgb': ('lin_srgb', '', filename.replace(extension, '_lin_srgb' + extension)),
        '_raw': ('raw', '', filename.replace(extension, '_raw' + extension))
    }

    for pattern, (space, options, new_name) in patterns.items():
        if re.search(pattern, filename, re.IGNORECASE):
            return space, options, new_name
    
    if extension.lower() in ['.jpg', '.png']:
        return 'srgb_texture', '', filename.replace(extension, f'_srgb_texture{extension}')
    
    return 'raw', '-d float', filename  # Default case for unrecognized formats

create_ui()