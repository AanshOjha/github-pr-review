from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import shutil
import xxhash
import os

# ==========================
# CHANGE THESE
# ==========================
ACCOUNT1 = Path(r"C:\Users\aansh\My Drive\Photos and Videos\Wallpapers")
ACCOUNT2 = Path(r"D:\Photos\Wallsy")
OUTPUT = Path(r"D:\Photos")
# ==========================

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif",
    ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".mp4", ".mov", ".avi", ".mkv", ".mts",
    ".m4v", ".3gp", ".webm", ".wmv"
}


def media_files(folder):
    return [
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS
    ]


def hash_file(path):
    h = xxhash.xxh3_128()

    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)

    return (path.stat().st_size, h.hexdigest())


def build_database(files):
    database = set()

    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        for result in tqdm(
            executor.map(hash_file, files),
            total=len(files),
            desc="Hashing"
        ):
            database.add(result)

    return database


print("Scanning Account 1...")
files1 = media_files(ACCOUNT1)

print("Scanning Account 2...")
files2 = media_files(ACCOUNT2)

print(f"\nAccount1 media: {len(files1):,}")
print(f"Account2 media: {len(files2):,}")

print("\nBuilding Account1 hash database...")
db1 = build_database(files1)

OUTPUT.mkdir(parents=True, exist_ok=True)

copied = 0

print("\nFinding unique files...")

with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:

    hashes = list(
        tqdm(
            executor.map(hash_file, files2),
            total=len(files2),
            desc="Comparing"
        )
    )

for file, fingerprint in zip(files2, hashes):

    if fingerprint not in db1:

        relative = file.relative_to(ACCOUNT2)
        destination = OUTPUT / relative

        destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(file, destination)

        copied += 1

print("\n==============================")
print(f"Unique files copied: {copied:,}")
print(f"Output folder: {OUTPUT}")
print("==============================")