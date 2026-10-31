# Double-click to start DR Model Swap (needs Python 3.9+ with numpy and Pillow: pip install -r requirements.txt).
# A small window shows the address; the tool itself opens in your browser. Close the window to stop it.
from drmodelswap.cli import main

if __name__ == '__main__':
    main(['gui'])
