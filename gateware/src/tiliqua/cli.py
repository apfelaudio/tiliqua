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
from vendor.ila                  import AsyncSerialILAFrontend

class CliAction(str, enum.Enum):
    Build    = "build"
    Simulate = "sim"

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
        parser.add_argument('--fw-spiflash-offset', type=str, default=None,
                            help="SoC designs: expect firmware flashed at this offset.")
        parser.add_argument('--fw-psram-offset', type=str, default="0x200000",
                            help="SoC designs: expect firmware in PSRAM at this offset.")
        # TODO: is this ok on windows?
        name_default = os.path.normpath(sys.argv[0]).split(os.sep)[2].replace("_", "-").upper()
        parser.add_argument('--name', type=str, default=name_default,
                            help="SoC designs: bitstream name to display at bottom of screen.")

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
        # Used during elaboration of the SoC to load the firmware binary into block RAM
        rust_fw_bin  = "firmware.bin"
        rust_fw_root = os.path.join(path, "fw")
        kwargs["firmware_bin_path"] = os.path.join(rust_fw_root, rust_fw_bin)
        if args.fw_spiflash_offset is not None:
            kwargs["spiflash_fw_offset"] = int(args.fw_spiflash_offset, 16)
        if args.fw_psram_offset is not None:
            kwargs["psram_fw_offset"] = int(args.fw_psram_offset, 16)
        kwargs["ui_name"] = args.name
        kwargs["ui_sha"]  = repo.head.object.hexsha[:6]

    if argparse_fragment:
        kwargs = kwargs | argparse_fragment(args)

    name = fragment.__name__ if callable(fragment) else fragment.__class__.__name__
    assert callable(fragment)
    fragment = fragment(**kwargs)

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

        # Generate firmware flashing arguments
        if "spiflash_fw_offset" in kwargs or "psram_fw_offset" in kwargs:
            fw_path = kwargs["firmware_bin_path"]
            fw_offset = (f"{args.fw_spiflash_offset}"
                         if "spiflash_fw_offset" in kwargs else
                         "<spiflash_offset_src>")
            args_flash_firmware = [
                "sudo", "openFPGALoader", "-c", "dirtyJtag", "-f", "-o", fw_offset,
                "--file-type", "raw", f"{fw_path}"
            ]

        # Optionally stop here if --fw-only is specified
        if args.fw_only:
            if args_flash_firmware:
                print("Flash firmware with:")
                print("\t$", ' '.join(args_flash_firmware))
                if args.flash:
                    subprocess.check_call(args_flash_firmware, env=os.environ)
            sys.exit(0)

        # Simulation configuration
        # By default, SoC examples share the same simulation harness.
        if sim_ports is None:
            sim_ports = sim.soc_simulation_ports
            sim_harness = os.path.join(path, "../selftest/sim.cpp")

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
        args_flash_bitstream = ["sudo", "openFPGALoader", "-c", "dirtyJtag", "-f", bitstream_path]
        bitstream_size = os.path.getsize(bitstream_path)
        print(f"Bitstream size (@ offset 0x0): {bitstream_size//1024} KiB")
        if args_flash_firmware:
            firmware_size = os.path.getsize(args_flash_firmware[-1])
            # TODO: relative assert based on 'slot' at which bitstream is flashed, not always zero!
            fw_offset = kwargs["spiflash_fw_offset"]
            assert fw_offset > bitstream_size, "firmware address overlaps bitstream!"
            print(f"Firmware size (@ offset {hex(fw_offset)}): {firmware_size//1024} KiB")
            print("Flash firmware with:")
            print("\t$", ' '.join(args_flash_firmware))
        print("Flash bitstream with:")
        print("\t$", ' '.join(args_flash_bitstream))

        if args.flash or hw_platform.ila:
            # ILA situation always requires flashing, as we want to make sure
            # we aren't getting data from an old bitstream before starting the
            # ILA frontend.
            if args_flash_firmware:
                subprocess.check_call(args_flash_firmware, env=os.environ)
            subprocess.check_call(args_flash_bitstream, env=os.environ)

        if hw_platform.ila:
            vcd_dst = "out.vcd"
            print(f"{AsyncSerialILAFrontend.__name__} listen on {args.ila_port} - destination {vcd_dst} ...")
            frontend = AsyncSerialILAFrontend(args.ila_port, baudrate=115200, ila=fragment.ila)
            frontend.emit_vcd(vcd_dst)

    return fragment
