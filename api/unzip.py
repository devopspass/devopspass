import sys
import zipfile

if len(sys.argv) != 3:
    print("Usage: python script.py <zip_file> <target_dir>")
    sys.exit(1)

zip_file = sys.argv[1]
target_dir = sys.argv[2]

with zipfile.ZipFile(zip_file, "r") as zip_ref:
    zip_ref.extractall(target_dir)

print(f"Successfully extracted contents of {zip_file} to {target_dir}")