import os
import pytesseract
from PIL import Image

_tesseract_path = r"C:\Program Files\Tesseract-OCR"
if os.path.isdir(_tesseract_path) and _tesseract_path not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _tesseract_path + ";" + os.environ.get("PATH", "")

img = Image.open("debug_ocr_test.png")
w, h = img.size

# Let's crop the entire top right quadrant (x from 1400 to 1920, y from 0 to 500)
top_right = img.crop((1400, 0, 1900, 400))
top_right.save("debug_top_right.png")
txt = pytesseract.image_to_string(top_right)
print("Top right text:")
print(txt)
