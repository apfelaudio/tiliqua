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

def flash_archive(archive_path, slot=None, noconfirm=False):
    """
    Flash a bitstream archive to the specified slot.
    For XIP firmware, slot must be None as it can only go in the bootloader slot (0x0).
    """
    commands_to_run = []
    
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
                
                # Collect commands for XIP firmware
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
            else:
                print(f"\nPreparing to flash bitstream to slot {slot}...")
                # Calculate addresses for this slot
                slot_base = SLOT_BITSTREAM_BASE + (slot * SLOT_SIZE)
                bitstream_addr = slot_base
                manifest_addr = (slot_base + SLOT_SIZE) - MANIFEST_SIZE
                firmware_base = FIRMWARE_BASE_SLOT0 + (slot * SLOT_SIZE)
                
                # Update manifest with firmware locations if needed
                for region in manifest["regions"]:
                    if "filename" not in region:
                        continue
                    if region.get("psram_dst") is not None:
                        region["spiflash_src"] = firmware_base
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