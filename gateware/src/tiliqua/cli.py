# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
Top-level CLI for Tiliqua projects, whether they include an SoC or not.
The set of available commands depends on the specific project.
"""
import argparse
import enum
import git
import logging
import os
import subprocess
import sys

from tiliqua                     import sim, video
from tiliqua.tiliqua_platform    import *
from tiliqua.tiliqua_soc         import TiliquaSoc
from tiliqua.types               import FirmwareLocation
from vendor.ila                  import AsyncSerialILAFrontend

class CliAction(str, enum.Enum):
    Build    = "build"
    Simulate = "sim"

MANIFEST_TEMPLATE = """{{
    "name": "{name}",
    "brief": "{brief}",
    "video": "{video}",
    {fw_img}
}},"""

MANIFEST_FW_TEMPLATE = """"fw_img":
    {{
        "spiflash_src": {fw_spiflash_src},
        "psram_dst": {fw_psram_dst},
        "size": {fw_size}
    }}"""

# TODO: these arguments would likely be cleaner encapsulated in a dataclass that
# has an instance per-project, that may also contain some bootloader metadata.
def top_level_cli(
    fragment,               # callable elaboratable class (to instantiate)
    video_core=True,        # project requires the video core (framebuffer, DVI output gen)
    path=None,              # project is located here (usually used for finding firmware)
    ila_supported=False,    # project supports compiling with internal ILA
    sim_ports=None,         # project has a list of simulation port names
    sim_harness=None,       # project has a .cpp simulation harness at this path
    argparse_callback=None, # project needs extra CLI flags before argparse.parse()
    argparse_fragment=None  # project needs to check args.<custom_flag> after argparse.parse()
    ):

    # Get some repository properties
    repo = git.Repo(search_parent_directories=True)

    # Configure logging.
    logging.getLogger().setLevel(logging.DEBUG)

    # Parse arguments
    parser = argparse.ArgumentParser()

    parser.add_argument('--flash', action='store_true',
                        help="Flash bitstream (and firmware if needed) after building it.")

    if video_core:
        parser.add_argument('--resolution', type=str, default="1280x720p60",
                            help="DVI resolution - (default: 1280x720p60)")
        parser.add_argument('--rotate-90', action='store_true',
                            help="Rotate DVI out by 90 degrees")

    if sim_ports or issubclass(fragment, TiliquaSoc):
        simulation_supported = True
        parser.add_argument('--trace-fst', action='store_true',
                            help="Simulation: enable dumping of traces to FST file.")
    else:
        simulation_supported = False

    if issubclass(fragment, TiliquaSoc):
        parser.add_argument('--svd-only', action='store_true',
                            help="SoC designs: stop after SVD generation")
        parser.add_argument('--pac-only', action='store_true',
                            help="SoC designs: stop after rust PAC generation")
        parser.add_argument('--fw-only', action='store_true',
                            help="SoC designs: stop after rust FW compilation (optionally re-flash)")
        parser.add_argument("--fw-location",
                            type=FirmwareLocation,
                            default=FirmwareLocation.SPIFlash.value,
                            choices=[
                                FirmwareLocation.BRAM.value,
                                FirmwareLocation.SPIFlash.value,
                                FirmwareLocation.PSRAM.value,
                            ],
                            help=(
                                "SoC designs: firmware (.text, .rodata) load strategy. `bram`: firmware "
                                "is baked into bram in the built bitstream. `spiflash`: firmware is "
                                "assumed flashed to spiflash at the provided `--fw-offset`. "
                                "'psram': firmware is assumed already copied to PSRAM (by a bootloader) "
                                "at the provided `--fw-offset`.")
                            )
        parser.add_argument('--fw-offset', type=str, default=None,
                            help="SoC designs: See `--fw-location`.")


    # TODO: is this ok on windows?
    name_default = os.path.normpath(sys.argv[0]).split(os.sep)[2].replace("_", "-").upper()
    parser.add_argument('--name', type=str, default=name_default,
                        help="Bitstream name to display in bootloader and bottom of screen.")
    parser.add_argument('--brief', type=str, default=None,
                        help="Brief description to display in bootloader.")
    parser.add_argument('--sc3', action='store_true',
                        help="platform override: Tiliqua R2 with a SoldierCrab R3")
    parser.add_argument('--hw3', action='store_true',
                        help="platform override: Tiliqua R3 with a SoldierCrab R3")
    parser.add_argument('--bootaddr', type=str, default="0x0",
                        help="'bootaddr' argument of ecppack (default: 0x0).")
    parser.add_argument('--verbose', action='store_true',
                        help="amaranth: enable verbose synthesis")
    parser.add_argument('--debug-verilog', action='store_true',
                        help="amaranth: emit debug verilog")
    parser.add_argument('--noflatten', action='store_true',
                        help="yosys: don't flatten heirarchy (useful for checking area usage).")
    if ila_supported:
        parser.add_argument('--ila', action='store_true',
                            help="debug: add ila to design, program bitstream after build, poll UART for data.")
        parser.add_argument('--ila-port', type=str, default="/dev/ttyACM0",
                            help="debug: serial port on host that ila is connected to")

    sim_action = [CliAction.Simulate.value] if simulation_supported else []
    parser.add_argument("action", type=CliAction,
                        choices=[CliAction.Build.value] + sim_action)

    if argparse_callback:
        argparse_callback(parser)

    # Print help if no arguments are passed.
    args = parser.parse_args(args=None if sys.argv[1:] else ["--help"])

    if args.action != CliAction.Build:
        assert args.flash == False, "--flash requires 'build' action"

    kwargs = {}

    if video_core:
        assert args.resolution in video.DVI_TIMINGS, f"error: video resolution must be one of {video.DVI_TIMINGS.keys()}"
        dvi_timings = video.DVI_TIMINGS[args.resolution]
        kwargs["dvi_timings"] = dvi_timings
        if args.rotate_90:
            kwargs["video_rotate_90"] = True

    if issubclass(fragment, TiliquaSoc):
        rust_fw_bin  = "firmware.bin"
        rust_fw_root = os.path.join(path, "fw")
        kwargs["firmware_bin_path"] = os.path.join(rust_fw_root, rust_fw_bin)
        kwargs["fw_location"] = args.fw_location
        if args.fw_offset is None:
            match args.fw_location:
                case FirmwareLocation.SPIFlash:
                    kwargs["fw_offset"] = 0xc0000
                    print("WARN: firmware loads from SPI flash, but no `--fw-offset` specified. "
                          f"using default: {hex(kwargs['fw_offset'])}")
                case FirmwareLocation.PSRAM:
                    kwargs["fw_offset"] = 0x200000
                    print("WARN: firmware loads from PSRAM, but no `--fw-offset` specified. "
                          f"using default: {hex(kwargs['fw_offset'])}")
        else:
            kwargs["fw_offset"] = int(args.fw_offset, 16)
        kwargs["ui_name"] = args.name
        kwargs["ui_sha"]  = repo.head.object.hexsha[:6]

    if argparse_fragment:
        kwargs = kwargs | argparse_fragment(args)

    assert callable(fragment)
    fragment = fragment(**kwargs)

    if args.brief is None:
        if hasattr(fragment, "brief"):
            args.brief = fragment.brief
        else:
            args.brief = ""

    if args.hw3:
        # Tiliqua R3 with SoldierCrab R3
        hw_platform = TiliquaR3SC3Platform()
    else:
        if args.sc3:
            # Tiliqua R2 with SoldierCrab R3
            hw_platform = TiliquaR2SC3Platform()
        else:
            # DEFAULT: Tiliqua R2 with SoldierCrab R2
            # default for now as this is the only version
            # that is actually in the wild.
            hw_platform = TiliquaR2SC2Platform()

    # (only used if firmware comes from SPI flash)
    args_flash_firmware = None

    build_path = "build"
    if not os.path.exists(build_path):
        os.makedirs(build_path)

    manifest_path = os.path.join(build_path, "manifest.json")

    if isinstance(fragment, TiliquaSoc):

        # Generate SVD
        svd_path = os.path.join(rust_fw_root, "soc.svd")
        fragment.gensvd(svd_path)
        if args.svd_only:
            sys.exit(0)

        # (re)-generate PAC (from SVD)
        TiliquaSoc.regenerate_pac_from_svd(svd_path)
        if args.pac_only:
            sys.exit(0)

        # Generate memory.x and some extra constants
        # Finally, build our stripped firmware image.
        fragment.genmem(os.path.join(rust_fw_root, "memory.x"))
        fragment.genconst("src/rs/lib/src/generated_constants.rs")
        TiliquaSoc.compile_firmware(rust_fw_root, rust_fw_bin)

        def maybe_flash_firmware(args, kwargs, force_flash=False):
            print()
            match args.fw_location:
                case FirmwareLocation.BRAM:
                    print("Note: Firmware is stored in BRAM, it is not possible to flash firmware "
                          "and bitstream separately. The bitstream contains the firmware.")
                case FirmwareLocation.SPIFlash:
                    args_flash_firmware = [
                        "sudo", "openFPGALoader", "-c", "dirtyJtag", "-f", "-o", f"{hex(kwargs['fw_offset'])}",
                        "--file-type", "raw", kwargs["firmware_bin_path"]
                    ]
                    print("Flash firmware with:")
                    print("\t$", ' '.join(args_flash_firmware))
                    if args.flash or force_flash:
                        subprocess.check_call(args_flash_firmware, env=os.environ)
                case FirmwareLocation.PSRAM:
                    args_flash_firmware = [
                        "sudo", "openFPGALoader", "-c", "dirtyJtag", "-f", "-o", "{{spiflash_src}}",
                        "--file-type", "raw", kwargs["firmware_bin_path"]
                    ]
                    firmware_size = os.path.getsize(kwargs["firmware_bin_path"])
                    print("This bitstream expects firmware already copied from SPI flash to PSRAM "
                          "by a bootloader. You must use the generated `manifest.json` launched by "
                          "the bootloader bitstream to start this bitstream.")
                    print()
                    print("Firmware may be flashed using a command like:")
                    print("\t$", ' '.join(args_flash_firmware))
                    print()
                    print("Where {{spiflash_src}} matches the value in the manifest.")

        with open(manifest_path, "w") as f:
            fw_template = ""
            match args.fw_location:
                case FirmwareLocation.SPIFlash:
                    fw_template = MANIFEST_FW_TEMPLATE.format(
                            fw_spiflash_src=kwargs["fw_offset"],
                            fw_psram_dst="null",
                            fw_size=os.path.getsize(kwargs["firmware_bin_path"]))
                case FirmwareLocation.PSRAM:
                    fw_template = MANIFEST_FW_TEMPLATE.format(
                            fw_spiflash_src="{{spiflash_src}}",
                            fw_psram_dst=kwargs["fw_offset"],
                            fw_size=os.path.getsize(kwargs["firmware_bin_path"]))
            f.write(MANIFEST_TEMPLATE.format(
                name=args.name, brief=args.brief, video=args.resolution,
                fw_img=fw_template))
            print("wrote manifest template:", manifest_path)

        # Optionally stop here if --fw-only is specified
        if args.fw_only:
            maybe_flash_firmware(args, kwargs)
            sys.exit(0)

        # Simulation configuration
        # By default, SoC examples share the same simulation harness.
        if sim_ports is None:
            sim_ports = sim.soc_simulation_ports
            sim_harness = os.path.join(path, "../selftest/sim.cpp")

    else:
        # Manifest generation with no SoC
        with open(manifest_path, "w") as f:
            f.write(MANIFEST_TEMPLATE.format(
                name=args.name,
                brief=args.brief,
                video=args.resolution if hasattr(args, 'resolution') else "<none>",
                fw_img=""))
            print("wrote manifest template:", manifest_path)


    if args.action == CliAction.Simulate:
        sim.simulate(fragment, sim_ports(fragment), sim_harness,
                     hw_platform, args.trace_fst)
        sys.exit(0)

    if ila_supported and args.ila:
        hw_platform.ila = True
    else:
        hw_platform.ila = False

    if args.action == CliAction.Build:

        build_flags = {
            "verbose": args.verbose,
            "debug_verilog": args.debug_verilog,
            "nextpnr_opts": "--timing-allow-fail",
            "ecppack_opts": f"--freq 38.8 --compress --bootaddr {args.bootaddr}"
        }

        if args.noflatten:
            # workaround for https://github.com/YosysHQ/yosys/issues/4349
            build_flags |= {
                "synth_opts": "-noflatten -run :coarse",
                "script_after_synth":
                    "proc; opt_clean -purge; synth_ecp5 -noflatten -top top -run coarse:",
            }

        print("Building bitstream for", hw_platform.name)

        hw_platform.build(fragment, **build_flags)

        # Print size information and some flashing instructions
        bitstream_path = "build/top.bit"
        bitstream_size = os.path.getsize(bitstream_path)
        print()
        print(f"Bitstream size: {bitstream_size//1024} KiB")
        if isinstance(fragment, TiliquaSoc):
            firmware_size = os.path.getsize(kwargs["firmware_bin_path"])
            print(f"Firmware size: {firmware_size//1024} KiB")

        args_flash_bitstream = ["sudo", "openFPGALoader", "-c", "dirtyJtag",
                                "-f", bitstream_path]
        print()
        print("Flash bitstream with command like:")
        print("\t$", ' '.join(args_flash_bitstream))

        if isinstance(fragment, TiliquaSoc):
            maybe_flash_firmware(args, kwargs, force_flash=hw_platform.ila)

        if args.flash or hw_platform.ila:
            # ILA situation always requires flashing, as we want to make sure
            # we aren't getting data from an old bitstream before starting the
            # ILA frontend.
            subprocess.check_call(args_flash_bitstream, env=os.environ)

        if hw_platform.ila:
            vcd_dst = "out.vcd"
            print(f"{AsyncSerialILAFrontend.__name__} listen on {args.ila_port} - destination {vcd_dst} ...")
            frontend = AsyncSerialILAFrontend(args.ila_port, baudrate=115200, ila=fragment.ila)
            frontend.emit_vcd(vcd_dst)

    return fragment
