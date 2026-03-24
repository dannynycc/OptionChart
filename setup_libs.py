"""
把官方 zip 裡的 libs/ 資料夾解壓到 OptionBridge\libs\
執行一次即可。
"""
import zipfile
import os
import shutil

ZIP_PATH = r"C:\Users\Home\OptionChart\CapitalAPI_2.13.58_PythonExample.zip"
# 備用路徑（zip 可能在桌面或其他地方）
ALT_PATHS = [
    r"C:\Users\Home\Desktop\CapitalAPI_2.13.58_PythonExample.zip",
    r"C:\Users\Home\Downloads\CapitalAPI_2.13.58_PythonExample.zip",
]

DEST = os.path.join(os.path.dirname(__file__), "libs")
LIBS_PREFIX = "CapitalAPI_2.13.58_PythonExample/SKDLLPythonTester/libs/"

# 找 zip
zip_path = None
for p in [ZIP_PATH] + ALT_PATHS:
    if os.path.exists(p):
        zip_path = p
        break

if not zip_path:
    print("找不到 zip 檔，請手動指定 ZIP_PATH")
    exit(1)

print(f"使用：{zip_path}")
os.makedirs(DEST, exist_ok=True)

with zipfile.ZipFile(zip_path) as z:
    extracted = 0
    for name in z.namelist():
        if name.startswith(LIBS_PREFIX) and not name.endswith('/'):
            filename = os.path.basename(name)
            dest_file = os.path.join(DEST, filename)
            with z.open(name) as src, open(dest_file, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            print(f"  解壓：{filename}")
            extracted += 1

print(f"\n完成，共解壓 {extracted} 個檔案到 {DEST}")
print(f"SKCOM.dll 路徑：{os.path.join(DEST, 'SKCOM.dll')}")
