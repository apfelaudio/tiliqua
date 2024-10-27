# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
Top-level CLI for Tiliqua projects, whether they include an SoC or not.
The set of available commands depends on the specific project.
"""
import argparse
import enum
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
    Show     = "show"

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

    # Configure logging.
    logging.getLogger().setLevel(logging.DEBUG)

    # Parse arguments
    parser = argparse.ArgumentParser()

    parser.add_argument('--flash', action='store_true',
                        help="Flash bitstream after building it.")

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
                            help="SoC designs: stop after rust FW compilation")

    parser.add_argument('--sc3', action='store_true',
                        help="Assume Tiliqua R2 with a SoldierCrab R3 (default: R2)")
    parser.add_argument('--bootaddr', type=str, default="0x0",
                        help="'bootaddr' argument of ecppack (default: 0x0).")
    parser.add_argument('--verbose', action='store_true',
                        help="amaranth: enable verbose synthesis")
    parser.add_argument('--debug-verilog', action='store_true',
                        help="amaranth: emit debug verilog")
    parser.add_argument('--noflatten', action='store_true',
                        help="yosys: don't flatten heirarchy (useful for checking area usage).")
    parser.add_argument('--show-top', type=str, default="top.core",
                        help="show: select fragment from design to plot, e.g. 'top.core.waveshaper0'.")
    if ila_supported:
        parser.add_argument('--ila', action='store_true',
                            help="debug: add ila to design, program bitstream after build, poll UART for data.")
        parser.add_argument('--ila-port', type=str, default="/dev/ttyACM0",
                            help="debug: serial port on host that ila is connected to")

    sim_action = [CliAction.Simulate.value] if simulation_supported else []
    parser.add_argument("action", type=CliAction,
                        choices=[CliAction.Build.value, CliAction.Show.value] + sim_action)

    if argparse_callback:
        argparse_callback(parser)

    # Print help if no arguments are passed.
    args = parser.parse_args(args=None if sys.argv[1:] else ["--help"])

    if args.action != CliAction.Build:
        assert args.flash == False, "--flash requires 'build' action"

    kwargs = {}

    if video_core:
        assert args.resolution in video.DVI_TIMINGS, f"error: video resolution must be one of {DVI_TIMINGS.keys()}"
        dvi_timings = video.DVI_TIMINGS[args.resolution]
        kwargs["dvi_timings"] = dvi_timings
        if args.rotate_90:
            kwargs["video_rotate_90"] = True

    if issubclass(fragment, TiliquaSoc):
        # Used during elaboration of the SoC to load the firmware binary into block RAM
        rust_fw_bin  = "firmware.bin"
        rust_fw_root = os.path.join(path, "fw")
        kwargs["firmware_bin_path"] = os.path.join(rust_fw_root, rust_fw_bin)

    if argparse_fragment:
        kwargs = kwargs | argparse_fragment(args)

    name = fragment.__name__ if callable(fragment) else fragment.__class__.__name__
    assert callable(fragment)
    fragment = fragment(**kwargs)

    if args.sc3:
        # Tiliqua R2 with SoldierCrab R3
        hw_platform = TiliquaR2SC3Platform()
    else:
        # Tiliqua R2 with SoldierCrab R2
        hw_platform = TiliquaR2SC2Platform()

    if args.action == CliAction.Show:
        # Convert the design to RTLIL. Passing no ports is fine as long as we're
        # inspecting something deeper than the top-level design (common case)
        from amaranth.back import rtlil
        rtlil_output = rtlil.convert(fragment, platform=sim.VerilatorPlatform(hw_platform), ports={})
        # Synthesize RTLIL at the provided --show-top path into .json
        from amaranth._toolchain.yosys import find_yosys
        yosys = find_yosys(lambda ver: ver >= (0, 40, 0))
        script = []
        script.append("read_ilang <<rtlil\n{}\nrtlil".format(rtlil_output))
        script.append(f"""
            hierarchy -top {args.show_top}
            prep
            """)
        filename = f"show.{args.show_top}"
        netlist_filename = f"{filename}.json"
        script.append("write_json " + netlist_filename)
        yosys.run(["-q", "-"], "\n".join(script), src_loc_at=1)
        # Plot netlist as an SVG and open it.
        os.system(f"netlistsvg {netlist_filename} -o {filename}.svg")
        os.system(f"xdg-open {filename}.svg")
        sys.exit(0)

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
        if args.fw_only:
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

        if args.flash or hw_platform.ila:
            # ILA situation always requires flashing, as we want to make sure
            # we aren't getting data from an old bitstream before starting the
            # ILA frontend.
            subprocess.check_call(["openFPGALoader",
                                   "-c", "dirtyJtag",
                                   "build/top.bit"],
                                  env=os.environ)
        if hw_platform.ila:
            vcd_dst = "out.vcd"
            print(f"{AsyncSerialILAFrontend.__name__} listen on {args.ila_port} - destination {vcd_dst} ...")
            frontend = AsyncSerialILAFrontend(args.ila_port, baudrate=115200, ila=fragment.ila)
            frontend.emit_vcd(vcd_dst)

    return fragment
