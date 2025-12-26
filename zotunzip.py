#!/usr/bin/env python3
"""
Zotero Backup Unzip Utility

Safely unzips multiple "Zotero-20251225T121037Z-*" ZIP files downloaded from Google Drive
to a target folder. Handles multi-part archives and ensures orderly extraction.

Usage:
    python zot_unzip.py <pattern> <output_dir> [--dry-run] [--verbose]

Example:
    python zot_unzip.py ~/Downloads/Zotero-20251225T121037Z- ~/Zotero_Restore
    python zot_unzip.py ./Backup- ./restored --dry-run
"""

import argparse
import hashlib
import logging
import os
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class ZipFileInfo:
    """Information about a ZIP file."""

    path: Path
    part_number: int
    size: int
    is_valid: bool = True
    error_message: Optional[str] = None


def build_zip_pattern(pattern: str) -> re.Pattern:
    """
    Build a regex pattern for matching ZIP files.

    Args:
        pattern: Base pattern (e.g., "Zotero-20251225T121037Z-")

    Returns:
        Compiled regex pattern
    """
    # Escape special regex characters in the pattern
    escaped = re.escape(pattern)
    # Match pattern followed by part number and .zip extension
    regex = f"^{escaped}(\\d+)\\.zip$"
    return re.compile(regex, re.IGNORECASE)


def find_zip_files(source_dir: Path, pattern: str) -> list[ZipFileInfo]:
    """
    Find all matching ZIP files in the source directory.

    Args:
        source_dir: Directory to search for ZIP files
        pattern: Base pattern to match (e.g., "Zotero-20251225T121037Z-")

    Returns:
        List of ZipFileInfo objects sorted by part number
    """
    zip_files: list[ZipFileInfo] = []
    zip_pattern = build_zip_pattern(pattern)

    if not source_dir.exists():
        logger.error(f"Source directory does not exist: {source_dir}")
        return zip_files

    for file_path in source_dir.iterdir():
        if not file_path.is_file():
            continue

        match = zip_pattern.match(file_path.name)
        if match:
            part_number = int(match.group(1))
            zip_info = ZipFileInfo(
                path=file_path,
                part_number=part_number,
                size=file_path.stat().st_size,
            )
            zip_files.append(zip_info)
            logger.debug(
                f"Found: {file_path.name} (Part {part_number}, {zip_info.size:,} bytes)"
            )

    # Sort by part number for orderly extraction
    zip_files.sort(key=lambda x: x.part_number)

    return zip_files


def validate_zip_file(zip_info: ZipFileInfo) -> bool:
    """
    Validate a ZIP file for integrity.

    Args:
        zip_info: ZipFileInfo object to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        with zipfile.ZipFile(zip_info.path, "r") as zf:
            # Test the archive integrity
            bad_file = zf.testzip()
            if bad_file is not None:
                zip_info.is_valid = False
                zip_info.error_message = f"Corrupted file in archive: {bad_file}"
                return False

        zip_info.is_valid = True
        return True

    except zipfile.BadZipFile as e:
        zip_info.is_valid = False
        zip_info.error_message = f"Bad ZIP file: {e}"
        return False
    except Exception as e:
        zip_info.is_valid = False
        zip_info.error_message = f"Validation error: {e}"
        return False


def check_sequence_continuity(zip_files: list[ZipFileInfo]) -> tuple[bool, str]:
    """
    Check if the ZIP file sequence is continuous (no missing parts).

    Args:
        zip_files: List of ZipFileInfo objects (should be sorted)

    Returns:
        Tuple of (is_continuous, message)
    """
    if not zip_files:
        return False, "No ZIP files found"

    part_numbers = [z.part_number for z in zip_files]
    expected_start = min(part_numbers)
    expected_end = max(part_numbers)
    expected_parts = set(range(expected_start, expected_end + 1))
    actual_parts = set(part_numbers)

    missing = expected_parts - actual_parts
    if missing:
        return False, f"Missing parts: {sorted(missing)}"

    return True, f"All parts present ({expected_start} to {expected_end})"


def safe_extract_member(
    zf: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    target_dir: Path,
    overwrite: bool = False,
) -> tuple[bool, str]:
    """
    Safely extract a single member from a ZIP file.

    Implements security checks to prevent zip slip attacks and other issues.

    Args:
        zf: ZipFile object
        member: ZipInfo member to extract
        target_dir: Target directory for extraction
        overwrite: Whether to overwrite existing files

    Returns:
        Tuple of (success, message)
    """
    # Security: Prevent zip slip attack (path traversal)
    member_path = Path(member.filename)

    # Check for absolute paths or parent directory references
    if member_path.is_absolute():
        return False, f"Skipping absolute path: {member.filename}"

    try:
        # Resolve the path and ensure it's within target_dir
        target_path = (target_dir / member_path).resolve()
        if not str(target_path).startswith(str(target_dir.resolve())):
            return False, f"Skipping path traversal attempt: {member.filename}"
    except Exception as e:
        return False, f"Invalid path {member.filename}: {e}"

    # Check if target already exists
    if target_path.exists() and not overwrite:
        if target_path.is_file():
            # Compare sizes - skip if same size (likely already extracted)
            if target_path.stat().st_size == member.file_size:
                return True, f"Skipped (already exists): {member.filename}"
            else:
                return False, f"File exists with different size: {member.filename}"

    # Create parent directories if needed
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Extract the member
    try:
        if member.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
        else:
            # Extract to a temporary file first, then move (atomic operation)
            with tempfile.NamedTemporaryFile(
                dir=target_path.parent, delete=False, suffix=".tmp"
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                try:
                    with zf.open(member) as source:
                        shutil.copyfileobj(source, tmp_file)

                    # Move temp file to final destination
                    tmp_path.replace(target_path)

                    # Preserve original file permissions if available
                    if member.external_attr:
                        mode = (member.external_attr >> 16) & 0o777
                        if mode:
                            target_path.chmod(mode)

                except Exception:
                    # Clean up temp file on error
                    if tmp_path.exists():
                        tmp_path.unlink()
                    raise

        return True, f"Extracted: {member.filename}"

    except Exception as e:
        return False, f"Failed to extract {member.filename}: {e}"


def extract_zip_file(
    zip_info: ZipFileInfo,
    target_dir: Path,
    overwrite: bool = False,
    verbose: bool = False,
) -> tuple[int, int, int]:
    """
    Extract a single ZIP file to the target directory.

    Args:
        zip_info: ZipFileInfo object for the file to extract
        target_dir: Target directory for extraction
        overwrite: Whether to overwrite existing files
        verbose: Whether to log verbose output

    Returns:
        Tuple of (extracted_count, skipped_count, error_count)
    """
    extracted = 0
    skipped = 0
    errors = 0

    logger.info(f"Extracting: {zip_info.path.name} ({zip_info.size:,} bytes)")

    try:
        with zipfile.ZipFile(zip_info.path, "r") as zf:
            members = zf.infolist()
            total_members = len(members)

            for i, member in enumerate(members, 1):
                success, message = safe_extract_member(
                    zf, member, target_dir, overwrite
                )

                if success:
                    if "Skipped" in message:
                        skipped += 1
                    else:
                        extracted += 1
                    if verbose:
                        logger.debug(message)
                else:
                    errors += 1
                    logger.warning(message)

                # Progress indicator for large archives
                if i % 1000 == 0 or i == total_members:
                    logger.info(f"  Progress: {i}/{total_members} files processed")

    except Exception as e:
        logger.error(f"Failed to process {zip_info.path.name}: {e}")
        errors += 1

    return extracted, skipped, errors


def unzip_all(
    source_dir: Path,
    target_dir: Path,
    pattern: str,
    dry_run: bool = False,
    validate: bool = True,
    overwrite: bool = False,
    verbose: bool = False,
) -> bool:
    """
    Main function to unzip all matching ZIP files.

    Args:
        source_dir: Directory containing the ZIP files
        target_dir: Directory to extract files to
        pattern: Base pattern to match ZIP files (e.g., "Zotero-20251225T121037Z-")
        dry_run: If True, only show what would be done
        validate: If True, validate ZIP files before extraction
        overwrite: If True, overwrite existing files
        verbose: If True, enable verbose logging

    Returns:
        True if successful, False otherwise
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("Zotero Backup Unzip Utility")
    logger.info("=" * 60)
    logger.info(f"Source directory: {source_dir}")
    logger.info(f"Target directory: {target_dir}")
    logger.info(f"File pattern: {pattern}*.zip")
    logger.info(f"Dry run: {dry_run}")
    logger.info(f"Validate archives: {validate}")
    logger.info(f"Overwrite existing: {overwrite}")
    logger.info("")

    # Find all ZIP files
    logger.info("Scanning for ZIP files...")
    zip_files = find_zip_files(source_dir, pattern)

    if not zip_files:
        logger.error("No matching ZIP files found!")
        logger.info(f"Looking for files matching pattern: {pattern}*.zip")
        return False

    logger.info(f"Found {len(zip_files)} ZIP file(s):")
    total_size = 0
    for zf in zip_files:
        logger.info(f"  Part {zf.part_number:03d}: {zf.path.name} ({zf.size:,} bytes)")
        total_size += zf.size
    logger.info(f"Total size: {total_size:,} bytes ({total_size / (1024**3):.2f} GB)")
    logger.info("")

    # Check sequence continuity
    is_continuous, continuity_msg = check_sequence_continuity(zip_files)
    if is_continuous:
        logger.info(f"Sequence check: {continuity_msg}")
    else:
        logger.warning(f"Sequence check: {continuity_msg}")
        response = input("Continue anyway? (y/N): ").strip().lower()
        if response != "y":
            logger.info("Aborted by user")
            return False
    logger.info("")

    # Validate ZIP files
    if validate:
        logger.info("Validating ZIP files...")
        all_valid = True
        for zf in zip_files:
            logger.info(f"  Validating: {zf.path.name}...")
            if validate_zip_file(zf):
                logger.info(f"    ✓ Valid")
            else:
                logger.error(f"    ✗ Invalid: {zf.error_message}")
                all_valid = False

        if not all_valid:
            logger.error(
                "Some ZIP files are invalid. Please re-download corrupted files."
            )
            return False
        logger.info("All ZIP files are valid!")
        logger.info("")

    # Dry run - just show what would be done
    if dry_run:
        logger.info("DRY RUN - No files will be extracted")
        logger.info(f"Would extract {len(zip_files)} ZIP files to: {target_dir}")
        return True

    # Create target directory
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Target directory ready: {target_dir}")
    except Exception as e:
        logger.error(f"Failed to create target directory: {e}")
        return False

    # Check available disk space (rough estimate)
    try:
        stat = shutil.disk_usage(target_dir)
        free_space = stat.free
        # Estimate: uncompressed size is typically 1-3x compressed size
        estimated_needed = total_size * 2
        if free_space < estimated_needed:
            logger.warning(
                f"Low disk space! Free: {free_space / (1024**3):.2f} GB, "
                f"Estimated needed: {estimated_needed / (1024**3):.2f} GB"
            )
            response = input("Continue anyway? (y/N): ").strip().lower()
            if response != "y":
                logger.info("Aborted by user")
                return False
    except Exception:
        pass  # Skip disk space check if it fails

    # Extract ZIP files in order
    logger.info("")
    logger.info("Starting extraction...")
    logger.info("-" * 40)

    total_extracted = 0
    total_skipped = 0
    total_errors = 0

    for i, zf in enumerate(zip_files, 1):
        logger.info(f"\n[{i}/{len(zip_files)}] Processing {zf.path.name}")

        extracted, skipped, errors = extract_zip_file(
            zf, target_dir, overwrite=overwrite, verbose=verbose
        )

        total_extracted += extracted
        total_skipped += skipped
        total_errors += errors

        logger.info(
            f"  Completed: {extracted} extracted, {skipped} skipped, {errors} errors"
        )

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("EXTRACTION COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total files extracted: {total_extracted:,}")
    logger.info(f"Total files skipped:   {total_skipped:,}")
    logger.info(f"Total errors:          {total_errors:,}")
    logger.info(f"Target directory:      {target_dir}")

    if total_errors > 0:
        logger.warning("Some errors occurred during extraction. Check the logs above.")
        return False

    logger.info("")
    logger.info("✓ All files extracted successfully!")
    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Safely unzip Zotero backup files from Google Drive",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s ~/Downloads/Zotero-20251225T121037Z- ~/Zotero_Restore
  %(prog)s ./Backup- ./restored --dry-run
  %(prog)s /path/to/MyArchive- ~/output --verbose --overwrite
        """,
    )

    parser.add_argument(
        "pattern",
        type=str,
        help="Path pattern for ZIP files (e.g., '~/Downloads/Zotero-20251225T121037Z-'). "
        "The directory is auto-inferred from the path.",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Output directory to extract files to",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be done without extracting",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip ZIP file validation (faster but less safe)",
    )
    parser.add_argument(
        "--overwrite",
        "-f",
        action="store_true",
        help="Overwrite existing files",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    # Parse pattern to extract source directory and base pattern
    pattern_path = Path(args.pattern).expanduser()

    # If the pattern contains a directory component, use it as source
    if (
        pattern_path.is_absolute()
        or "/" in args.pattern
        or args.pattern.startswith("~")
    ):
        source_dir = pattern_path.parent.resolve()
        base_pattern = pattern_path.name
    else:
        # Pattern is just a filename prefix, use current directory
        source_dir = Path.cwd()
        base_pattern = args.pattern

    target_dir = args.output.expanduser().resolve()

    # Run the unzip operation
    success = unzip_all(
        source_dir=source_dir,
        target_dir=target_dir,
        pattern=base_pattern,
        dry_run=args.dry_run,
        validate=not args.no_validate,
        overwrite=args.overwrite,
        verbose=args.verbose,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
