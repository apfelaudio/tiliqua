import argparse
import logging
import os
import shutil
import subprocess
import sys

from dataclasses                 import dataclass

from tiliqua                     import sim, video
from tiliqua.tiliqua_platform    import *
from tiliqua.tiliqua_soc         import TiliquaSoc
from vendor.ila                  import AsyncSerialILAFrontend

def soc_regenerate_pac_from_svd(svd_path):
    """
    Generate Rust PAC from an SVD.
    Currently all SoC reuse the same `pac_dir`, however this
    should become local to each SoC at some point.
    """
    pac_dir = "src/rs/pac"
    pac_build_dir = os.path.join(pac_dir, "build")
    pac_gen_dir   = os.path.join(pac_dir, "src/generated")
    src_genrs     = os.path.join(pac_dir, "src/generated.rs")
    shutil.rmtree(pac_build_dir, ignore_errors=True)
    shutil.rmtree(pac_gen_dir, ignore_errors=True)
    os.makedirs(pac_build_dir)
    if os.path.isfile(src_genrs):
        os.remove(src_genrs)

    subprocess.check_call([
        "svd2rust",
        "-i", svd_path,
        "-o", pac_build_dir,
        "--target", "riscv",
        "--make_mod",
        "--ident-formats-theme", "legacy"
        ], env=os.environ)

    shutil.move(os.path.join(pac_build_dir, "mod.rs"), src_genrs)
    shutil.move(os.path.join(pac_build_dir, "device.x"),
                os.path.join(pac_dir,       "device.x"))

    subprocess.check_call([
        "form",
        "-i", src_genrs,
        "-o", pac_gen_dir,
        ], env=os.environ)

    shutil.move(os.path.join(pac_gen_dir, "lib.rs"), src_genrs)

    subprocess.check_call([
        "cargo", "fmt", "--", "--emit", "files"
        ], env=os.environ, cwd=pac_dir)

    print("Rust PAC updated at ...", pac_dir)

def soc_compile_firmware(path):
    subprocess.check_call([
        "cargo", "build", "--release"
        ], env=os.environ, cwd=os.path.join(path, "fw"))
    subprocess.check_call([
        "cargo", "objcopy", "--release", "--", "-Obinary", "firmware.bin"
        ], env=os.environ, cwd=os.path.join(path, "fw"))

def top_level_cli(
    fragment,
    video_core=True,
    path=None,
    ila_supported=False,
    sim_ports=None,
    sim_harness=None,
    argparse_callback=None,
    argparse_fragment=None
    ):

    # Configure logging.
    logging.getLogger().setLevel(logging.DEBUG)

    # Parse arguments
    parser = argparse.ArgumentParser()

    if video_core:
        parser.add_argument('--resolution', type=str, default="1280x720p60",
                            help="DVI resolution - (default: 1280x720p60)")
        parser.add_argument('--rotate-90', action='store_true',
                            help="Rotate DVI out by 90 degrees")

    if sim_ports or issubclass(fragment, TiliquaSoc):
        parser.add_argument('--sim', action='store_true',
                            help="Simulate the design with Verilator")
        parser.add_argument('--trace-fst', action='store_true',
                            help="Simulation: enable dumping of traces to FST file.")

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
    if ila_supported:
        parser.add_argument('--ila', action='store_true',
                            help="debug: add ila to design, program bitstream after build, poll UART for data.")
        parser.add_argument('--ila-port', type=str, default="/dev/ttyACM0",
                            help="debug: serial port on host that ila is connected to")

    if argparse_callback:
        argparse_callback(parser)

    args = parser.parse_args()

    if args.verbose:
        os.environ["AMARANTH_verbose"] = "1"

    if args.debug_verilog:
        os.environ["AMARANTH_debug_verilog"] = "1"

    os.environ["AMARANTH_nextpnr_opts"] = "--timing-allow-fail"
    os.environ["AMARANTH_ecppack_opts"] = f"--freq 38.8 --compress --bootaddr {args.bootaddr}"


    kwargs = {}

    if video_core:
        assert args.resolution in video.DVI_TIMINGS, f"error: video resolution must be one of {DVI_TIMINGS.keys()}"
        dvi_timings = video.DVI_TIMINGS[args.resolution]
        kwargs["dvi_timings"] = dvi_timings
        if args.rotate_90:
            kwargs["video_rotate_90"] = True

    if issubclass(fragment, TiliquaSoc):
        kwargs["firmware_path"] = os.path.join(path, "fw/firmware.bin")

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

    if isinstance(fragment, TiliquaSoc):

        # Generate SVD
        svd_path = os.path.join(path, "fw/soc.svd")
        fragment.gensvd(svd_path)
        if args.svd_only:
            sys.exit(0)

        # (re)-generate PAC (from SVD)
        soc_regenerate_pac_from_svd(svd_path)
        if args.pac_only:
            sys.exit(0)

        # Generate memory.x and some extra constants
        # Finally, build our stripped firmware image.
        fragment.genmem(os.path.join(path, "fw/memory.x"))
        fragment.genconst("src/rs/lib/src/generated_constants.rs")
        soc_compile_firmware(path)
        if args.fw_only:
            sys.exit(0)

        # Simulation configuration
        # By default, SoC examples share the same simulation harness.
        if sim_ports is None:
            sim_ports = sim.soc_simulation_ports
            sim_harness = os.path.join(path, "../selftest/sim.cpp")

    if sim_ports and args.sim:
        sim.simulate(fragment, sim_ports(fragment), sim_harness,
                     hw_platform, args.trace_fst)
        sys.exit(0)

    if ila_supported and args.ila:
        hw_platform.ila = True
    else:
        hw_platform.ila = False

    print("Building bitstream for", hw_platform.name)
    hw_platform.build(fragment)

    if hw_platform.ila:
        subprocess.check_call(["openFPGALoader",
                               "-c", "dirtyJtag",
                               "build/top.bit"],
                              env=os.environ)
        vcd_dst = "out.vcd"
        print(f"{AsyncSerialILAFrontend.__name__} listen on {args.ila_port} - destination {vcd_dst} ...")
        frontend = AsyncSerialILAFrontend(args.ila_port, baudrate=115200, ila=fragment.ila)
        frontend.emit_vcd(vcd_dst)

    return fragment
