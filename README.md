# TX Converter
 Converts textures to arnold tx and renderman tex.
 
Features
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
 

![image](https://github.com/user-attachments/assets/6a6834b0-7146-4a19-9b8c-e77cc33164d8)




