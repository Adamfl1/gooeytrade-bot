import os
import pytesseract
from PIL import Image

_tesseract_path = r"C:\Program Files\Tesseract-OCR"
if os.path.isdir(_tesseract_path) and _tesseract_path not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _tesseract_path + ";" + os.environ.get("PATH", "")

img = Image.open("debug_ocr_test.png")
# Test a taller and wider crop around the top right
crop_box = (1550, 30, 1850, 220)
cropped = img.crop(crop_box)
cropped.save("debug_tv_crop_large.png")

print(f"Large crop size: {cropped.size}")
txt = pytesseract.image_to_string(cropped, config="--psm 6")
print("OCR on large crop:")
print(repr(txt))
print(txt)
