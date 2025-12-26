import os
import re
from datetime import datetime

import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError

# make sure python 3.13+ is used to run this script
if sys.version_info < (3, 13):
    raise Exception("This script requires Python 3.13 or higher.")


def extract_base_name(filename):
    """
    Extract base name from filename.
    Handles patterns like:
    - 'name.pdf' -> 'name'
    - 'name 1.pdf' -> 'name'
    - 'name 123.pdf' -> 'name'
    """
    # Remove extension
    name_without_ext = os.path.splitext(filename)[0]
    # Remove trailing space + number pattern
    base_name = re.sub(r"\s+\d+$", "", name_without_ext)
    return base_name.lower()


def copy_single_file(src_file_path, dest_file_path):
    shutil.copy2(src_file_path, dest_file_path)
    return os.path.basename(dest_file_path)


def copy_files(src_dir, dest_dir, file_extension, max_workers=3, timeout_seconds=0.5):
    if os.path.exists(dest_dir):
        print(f"Destination directory '{dest_dir}' already exists.")
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)

    # Collect all files with their modification times
    all_files = []
    for root, _, files in os.walk(src_dir):
        for file in files:
            if file_extension == ".*" or file.endswith(file_extension):
                src_file_path = os.path.join(root, file)
                mtime = os.path.getmtime(src_file_path)
                all_files.append((file, src_file_path, mtime))

    # Sort by modification time (newest first) - ensures we keep the newest file for each base name
    all_files.sort(key=lambda x: x[2], reverse=True)

    files_to_copy = []
    seen_base_names = {}  # {base_name: (filename, src_path, mtime)}
    duplicates = {}  # {original_file: [list of (duplicate_src, mtime)]}

    for file, src_file_path, mtime in all_files:
        base_name = extract_base_name(file)

        if base_name in seen_base_names:
            # This is an older duplicate - skip it
            original_name, _, _ = seen_base_names[base_name]
            if original_name not in duplicates:
                duplicates[original_name] = []
            duplicates[original_name].append((src_file_path, mtime))
        else:
            # First occurrence is the newest (due to sorting by mtime descending)
            seen_base_names[base_name] = (file, src_file_path, mtime)
            dest_file_path = os.path.join(dest_dir, file)
            files_to_copy.append((src_file_path, dest_file_path))

    # Sort duplicates by modification time (newest first) for display
    for original in duplicates:
        duplicates[original].sort(key=lambda x: x[1], reverse=True)

    copied_files = []
    skipped_files = []  # List of (src_path, reason) for files that were skipped

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i, (src, dest) in enumerate(files_to_copy):
            future = executor.submit(copy_single_file, src, dest)
            try:
                result = future.result(timeout=timeout_seconds)
                copied_files.append(result)
            except TimeoutError:
                skipped_files.append((src, f"Timeout after {timeout_seconds}s"))
                print(f"SKIPPED (timeout): {src}")
            except Exception as e:
                skipped_files.append((src, str(e)))
                print(f"SKIPPED (error): {src} - {e}")

            if (i + 1) % 100 == 0:
                print(
                    f"Processed {i + 1} / {len(files_to_copy)} files, "
                    f"copied: {len(copied_files)}, skipped: {len(skipped_files)}"
                )

    print(f"\nTotal files copied: {len(copied_files)}")
    print(f"Total duplicates skipped: {sum(len(v) for v in duplicates.values())}")
    print(f"Total files skipped due to timeout/error: {len(skipped_files)}")

    if skipped_files:
        print("\n" + "=" * 50)
        print("SKIPPED FILES:")
        print("=" * 50)
        for src_path, reason in skipped_files:
            print(f"  - {src_path}")
            print(f"    Reason: {reason}")

    return copied_files, duplicates, skipped_files


def main():
    src_dir = "/Users/trungnt13/Downloads/Zotero"
    dst_dir = "/Users/trungnt13/Downloads/zotero_pdfs"
    _, _, _ = copy_files(src_dir, dst_dir, ".*", timeout_seconds=30)


if __name__ == "__main__":
    main()
