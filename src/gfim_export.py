"""
Get the downloaded GFIM reports out of Colab and onto your PC.

Run this AFTER gfim_download.py, in the same Colab session.
It zips each year folder separately and sends each zip to your browser, so
you end up with GFIM_2023.zip, GFIM_2024.zip ... in your Downloads folder.

In a Colab cell:

    from gfim_export import export
    export()                       # every year found, one zip each
    export([2023, 2024])           # only these years
    export(single_zip=True)        # one GFIM_Reports.zip with everything
    export(to_drive=True)          # copy to Google Drive instead of downloading

Outside Colab (plain Python on your own machine) the zips are just written
next to the data and nothing is sent anywhere.
"""

from __future__ import annotations

import os
import shutil
import zipfile

BASE = "GFIM_Reports"          # must match BASE in gfim_download.py
ZIP_DIR = "GFIM_Zips"          # where the zip files are written
DRIVE_DIR = "/content/drive/MyDrive/GFIM_Reports"


def in_colab() -> bool:
    try:
        import google.colab  # noqa: F401
        return True
    except ImportError:
        return False


def year_folders(base: str = BASE):
    """Year sub-folders that actually contain files, oldest first."""
    if not os.path.isdir(base):
        raise FileNotFoundError(
            f"'{base}' not found. Run gfim_download.py first, in this session.")
    out = []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if name.isdigit() and os.path.isdir(path) and os.listdir(path):
            out.append((int(name), path))
    return out


def zip_folder(src: str, zip_path: str) -> str:
    """Zip one folder, keeping the year as the top-level folder inside."""
    os.makedirs(os.path.dirname(zip_path) or ".", exist_ok=True)
    top = os.path.basename(src.rstrip(os.sep))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, src)
                z.write(full, os.path.join(top, rel))
    return zip_path


def human(n: int) -> str:
    return f"{n/1024/1024:.1f} MB" if n > 1024 * 1024 else f"{n/1024:.0f} KB"


def export(years=None, single_zip: bool = False, to_drive: bool = False,
           base: str = BASE, zip_dir: str = ZIP_DIR):
    """Zip the year folders and hand them to the browser (or to Drive)."""
    folders = year_folders(base)
    if years:
        wanted = {int(y) for y in years}
        folders = [(y, p) for y, p in folders if y in wanted]
    if not folders:
        print("Nothing to export.")
        return []

    for y, p in folders:
        print(f"  {y}: {len(os.listdir(p))} files")

    if to_drive:
        return _to_drive(folders)

    os.makedirs(zip_dir, exist_ok=True)
    zips = []

    if single_zip:
        path = os.path.join(zip_dir, "GFIM_Reports.zip")
        print("\nZipping everything into one file ...")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for _, folder in folders:
                for root, _, files in os.walk(folder):
                    for f in files:
                        full = os.path.join(root, f)
                        z.write(full, os.path.relpath(full, base))
            manifest = os.path.join(base, "_manifest.csv")
            if os.path.exists(manifest):
                z.write(manifest, "_manifest.csv")
        zips.append(path)
    else:
        print("\nZipping one file per year ...")
        for y, folder in folders:
            path = os.path.join(zip_dir, f"GFIM_{y}.zip")
            zip_folder(folder, path)
            zips.append(path)

    for p in zips:
        print(f"  {p}  ({human(os.path.getsize(p))})")

    if in_colab():
        from google.colab import files
        print("\nSending to your browser. Allow multiple downloads if asked.")
        for p in zips:
            files.download(p)
    else:
        print("\nNot running in Colab, so nothing was sent. "
              f"The zips are in '{os.path.abspath(zip_dir)}'.")

    return zips


def _to_drive(folders):
    """Copy the year folders to Google Drive instead of downloading them."""
    if not in_colab():
        print("to_drive only works in Colab.")
        return []
    from google.colab import drive
    if not os.path.isdir("/content/drive"):
        drive.mount("/content/drive")

    copied = []
    for y, folder in folders:
        dest = os.path.join(DRIVE_DIR, str(y))
        os.makedirs(dest, exist_ok=True)
        n = 0
        for f in os.listdir(folder):
            src = os.path.join(folder, f)
            dst = os.path.join(dest, f)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                n += 1
        print(f"  {y}: copied {n} new files to {dest}")
        copied.append(dest)
    print("\nOpen Google Drive on your PC (or the Drive desktop app) "
          "to pull them down.")
    return copied


if __name__ == "__main__":
    export()
