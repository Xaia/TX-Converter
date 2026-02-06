# TX Converter
 Converts textures to arnold tx renderman tex and karma rat.
 
Features:
 - Convert entire folders and subfolders
 - Multithreaded conversion
 - Simultaneous Multi texture conversion
 - Drag and drop
 - DWAA Compression
 - Adding Missing color space suffix to original and tx/tex
 - Adding color space Acescg to tx/tex to acescg
 - Renderman Bump Rough support
 - HDRI checkbox
 - Automatic color space detection
 - Acescg 1.3 and Acescg 1.0.3 support
 - Texture Color space preview
 - Custom settings
 - Custom patterns
 - Added an Icon
 - Added Houdini .RAT support
 - Added Custom ENV VARS

 How to compile:
 Pyside 6 is required

1. Install PyInstaller

First, make sure you have Python (and pip) installed on your Windows machine. Then, open a Command Prompt and install PyInstaller using pip:

```pip install pyinstaller```

2. Generate the Executable

Navigate to the directory containing your script in the Command Prompt:

```cd path\to\the\main.py```

Then, run PyInstaller with the following command:

```pyinstaller --onefile --noconsole main.py```


Env Vars

 set RMANTREE = path/to/RenderManForMaya-26.3/
 
 set MAKETX_PATH = path/to/arnold/maketx.exe 
 
 set OCIO = path/to/config.ocio

 set IMAKETEX = path/to/houdini/imaketx.exe

 set HFS = path/to/houdini/
 <img width="694" height="692" alt="tx_icon2" src="https://github.com/user-attachments/assets/370c8642-d639-471e-b780-dfa9697952fa" />


<img width="869" height="1407" alt="image" src="https://github.com/user-attachments/assets/996492d7-6d64-4301-82e1-66f2d5655e1a" />





