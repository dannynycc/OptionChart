import pefile
pe = pefile.PE(r"C:\Program Files\群益API\SKCOM.dll")
for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
    if exp.name:
        print(exp.name.decode())
