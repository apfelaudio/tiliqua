#!/usr/bin/env python3

"""
Flash tool for Tiliqua bitstream archives.
"""

import argparse
import json
import os
import subprocess
import sys
import tarfile

# Flash memory map constants
BOOTLOADER_BITSTREAM_ADDR = 0x000000
SLOT_BITSTREAM_BASE      = 0x100000  # First user slot starts here
SLOT_SIZE                = 0x100000
MANIFEST_SIZE            = 1024
FIRMWARE_BASE_SLOT0      = 0x1C0000
MAX_SLOTS                = 8
FLASH_PAGE_SIZE          = 1024 # 1KB

def flash_file(file_path, offset, file_type="auto", dry_run=True):
    """Flash a file to the specified offset using openFPGALoader."""
    cmd = [
        "sudo", "openFPGALoader", "-c", "dirtyJtag",
        "-f", "-o", f"{hex(offset)}",
    ]
    if file_type != "auto":
        cmd.extend(["--file-type", file_type])
    cmd.append(file_path)
    
    if dry_run:
        return cmd
    else:
        print(f"Flashing to {hex(offset)}:")
        print("\t$", " ".join(cmd))
        subprocess.check_call(cmd)

def check_region_overlaps(regions, slot=None):
    """
    Check for overlapping regions in flash commands and slot boundaries.
    Each region is aligned up before checking.
    Returns (bool, str) tuple: (has_overlap, error_message)
    """
    # Convert regions to (start, end) tuples, with aligned sizes
    aligned_regions = []
    for r in regions:
        start = r['addr']
        size = (r['size'] + FLASH_PAGE_SIZE - 1) & ~(FLASH_PAGE_SIZE - 1)  # Align up
        end = start + size
        aligned_regions.append((start, end, r['name']))

        # For non-XIP firmware, check if any region exceeds its slot
        if slot is not None:
            slot_start = (start // SLOT_SIZE) * SLOT_SIZE
            slot_end = slot_start + SLOT_SIZE
            if end > slot_end:
                return (True, f"Region '{r['name']}' exceeds slot boundary: "
                            f"ends at 0x{end:x}, slot ends at 0x{slot_end:x}")

    # Sort by start address
    aligned_regions.sort()

    # Check for overlaps
    for i in range(len(aligned_regions) - 1):
        curr_end = aligned_regions[i][1]
        next_start = aligned_regions[i + 1][0]
        if curr_end > next_start:
            return (True, f"Overlap detected between '{aligned_regions[i][2]}' (ends at 0x{curr_end:x}) "
                         f"and '{aligned_regions[i+1][2]}' (starts at 0x{next_start:x})")
    return (False, "")

def flash_archive(archive_path, slot=None, noconfirm=False):
    """
    Flash a bitstream archive to the specified slot.
    For XIP firmware, slot must be None as it can only go in the bootloader slot (0x0).
    """
    commands_to_run = []
    regions_to_check = []

    # Extract archive to temporary location
    with tarfile.open(archive_path, "r:gz") as tar:
        # Read manifest first
        manifest_info = tar.getmember("manifest.json")
        manifest_f = tar.extractfile(manifest_info)
        manifest = json.load(manifest_f)

        # Check if this is an XIP firmware
        has_xip_firmware = False
        if manifest.get("regions"):
            for region in manifest["regions"]:
                if region.get("spiflash_src") is not None:
                    has_xip_firmware = True
                    xip_offset = region["spiflash_src"]
                    break

        if has_xip_firmware:
            if slot is not None:
                print("Error: XIP firmware bitstreams must be flashed to bootloader slot")
                print(f"Remove --slot argument to flash at 0x0 with firmware at 0x{xip_offset:x}")
                sys.exit(1)
        else:
            if slot is None:
                print("Error: Must specify slot for non-XIP firmware bitstreams")
                sys.exit(1)

        # Create temp directory for extracted files
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tar.extractall(tmpdir)

            if has_xip_firmware:
                print("\nPreparing to flash XIP firmware bitstream to bootloader slot...")
                # Collect commands for bootloader location
                commands_to_run.append(
                    flash_file(
                        os.path.join(tmpdir, "top.bit"),
                        BOOTLOADER_BITSTREAM_ADDR,
                        dry_run=True
                    )
                )
                # Add bootloader bitstream region
                regions_to_check.append({
                    'addr': BOOTLOADER_BITSTREAM_ADDR,
                    'size': os.path.getsize(os.path.join(tmpdir, "top.bit")),
                    'name': 'bootloader bitstream'
                })

                # Collect commands for XIP firmware
                # Add XIP firmware regions
                for region in manifest["regions"]:
                    if "filename" not in region:
                        continue
                    commands_to_run.append(
                        flash_file(
                            os.path.join(tmpdir, region["filename"]),
                            region["spiflash_src"],
                            "raw",
                            dry_run=True
                        )
                    )
                    regions_to_check.append({
                        'addr': region["spiflash_src"],
                        'size': region["size"],
                        'name': f"firmware '{region['filename']}'"
                    })
            else:
                print(f"\nPreparing to flash bitstream to slot {slot}...")
                # Calculate addresses for this slot
                slot_base = SLOT_BITSTREAM_BASE + (slot * SLOT_SIZE)
                bitstream_addr = slot_base
                manifest_addr = (slot_base + SLOT_SIZE) - MANIFEST_SIZE
                firmware_base = FIRMWARE_BASE_SLOT0 + (slot * SLOT_SIZE)

                # Add bitstream region
                regions_to_check.append({
                    'addr': bitstream_addr,
                    'size': os.path.getsize(os.path.join(tmpdir, "top.bit")),
                    'name': 'bitstream'
                })

                # Add manifest region
                regions_to_check.append({
                    'addr': manifest_addr,
                    'size': MANIFEST_SIZE,
                    'name': 'manifest'
                })

                # Update manifest and add firmware regions
                for region in manifest["regions"]:
                    if "filename" not in region:
                        continue
                    if region.get("psram_dst") is not None:
                        assert region["spiflash_src"] is None
                        region["spiflash_src"] = firmware_base
                        print(f"manifest: region {region['filename']}: spiflash_src set to 0x{firmware_base:x}")
                        regions_to_check.append({
                            'addr': firmware_base,
                            'size': region["size"],
                            'name': region['filename'],
                        })
                        firmware_base += region["size"]
                        firmware_base = (firmware_base + 0xFFF) & ~0xFFF

                # Write updated manifest
                manifest_path = os.path.join(tmpdir, "manifest.json")
                print(f"\nFinal manifest contents:\n{json.dumps(manifest, indent=2)}")
                with open(manifest_path, "w") as f:
                    json.dump(manifest, f)

                # Collect all commands
                commands_to_run.append(
                    flash_file(
                        os.path.join(tmpdir, "top.bit"),
                        bitstream_addr,
                        dry_run=True
                    )
                )
                commands_to_run.append(
                    flash_file(
                        manifest_path,
                        manifest_addr,
                        "raw",
                        dry_run=True
                    )
                )
                for region in manifest["regions"]:
                    if "filename" not in region or "spiflash_src" not in region:
                        continue
                    commands_to_run.append(
                        flash_file(
                            os.path.join(tmpdir, region["filename"]),
                            region["spiflash_src"],
                            "raw",
                            dry_run=True
                        )
                    )

            # Print all regions
            print("\nRegions to be flashed:")
            for region in sorted(regions_to_check, key=lambda r: r['addr']):
                aligned_size = (region['size'] + FLASH_PAGE_SIZE - 1) & ~(FLASH_PAGE_SIZE - 1)
                print(f"  {region['name']}:")
                print(f"    start: 0x{region['addr']:x}")
                print(f"    end:   0x{region['addr']+aligned_size-1:x}")

            # Check for overlaps before proceeding
            has_overlap, error_msg = check_region_overlaps(regions_to_check, slot)
            if has_overlap:
                print(f"Error: {error_msg}")
                sys.exit(1)

            # Show all commands and get confirmation
            print("\nThe following commands will be executed:")
            for cmd in commands_to_run:
                print("\t$", " ".join(cmd))

            if not noconfirm:
                response = input("\nProceed with flashing? [y/N] ")
                if response.lower() != 'y':
                    print("Aborting.")
                    sys.exit(0)

            # Execute all commands
            print("\nExecuting flash commands...")
            for cmd in commands_to_run:
                subprocess.check_call(cmd)

            print("\nFlashing completed successfully")

def main():
    parser = argparse.ArgumentParser(description="Flash Tiliqua bitstream archives")
    parser.add_argument("archive", help="Path to bitstream archive (.tar.gz)")
    parser.add_argument("--slot", type=int, help="Slot number (0-7) for bootloader-managed bitstreams")
    parser.add_argument("--noconfirm", action="store_true", help="Do not ask for confirmation before flashing")

    args = parser.parse_args()

    if not os.path.exists(args.archive):
        print(f"Error: Archive not found: {args.archive}")
        sys.exit(1)

    if args.slot is not None and not 0 <= args.slot < MAX_SLOTS:
        print(f"Error: Slot must be between 0 and {MAX_SLOTS-1}")
        sys.exit(1)

    flash_archive(args.archive, args.slot, args.noconfirm)

if __name__ == "__main__":
    main() 
