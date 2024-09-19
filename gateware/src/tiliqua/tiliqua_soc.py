# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# Based on some work from LUNA project licensed under BSD. Anything new
# in this file is issued under the following license:
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import argparse
import logging
import os
import shutil
import subprocess
import sys

from amaranth                                    import *
from amaranth.build                              import Attrs, Pins, PinsN, Platform, Resource, Subsignal
from amaranth.hdl.rec                            import Record
from amaranth.lib                                import wiring, data
from amaranth.lib.wiring                         import Component, In, Out, flipped, connect

from amaranth_soc                                import csr, gpio, wishbone
from amaranth_soc.csr.wishbone                   import WishboneCSRBridge

from vendor.soc.cores                            import sram, timer, uart
from vendor.soc.cpu                              import InterruptController, VexRiscv
from vendor.soc                                  import readbin
from vendor.soc.generate                         import GenerateSVD

from tiliqua.tiliqua_platform                    import *

from tiliqua                                     import psram_peripheral, i2c, encoder, dtr, video, eurorack_pmod_peripheral
from tiliqua                                     import sim, eurorack_pmod

from example_vectorscope.top                     import Persistance

TILIQUA_CLOCK_SYNC_HZ = int(60e6)

class VideoPeripheral(wiring.Component):

    class PersistReg(csr.Register, access="w"):
        persist: csr.Field(csr.action.W, unsigned(16))

    class DecayReg(csr.Register, access="w"):
        decay: csr.Field(csr.action.W, unsigned(8))

    class PaletteReg(csr.Register, access="w"):
        position: csr.Field(csr.action.W, unsigned(8))
        red:      csr.Field(csr.action.W, unsigned(8))
        green:    csr.Field(csr.action.W, unsigned(8))
        blue:     csr.Field(csr.action.W, unsigned(8))

    class PaletteBusyReg(csr.Register, access="r"):
        busy: csr.Field(csr.action.R, unsigned(1))

    def __init__(self, fb_base, fb_size, bus_dma, video):
        self.en = Signal()
        self.video = video
        self.persist = Persistance(
            fb_base=fb_base, bus_master=bus_dma.bus, fb_size=fb_size)
        bus_dma.add_master(self.persist.bus)

        regs = csr.Builder(addr_width=5, data_width=8)

        self._persist      = regs.add("persist",      self.PersistReg(),     offset=0x0)
        self._decay        = regs.add("decay",        self.DecayReg(),       offset=0x4)
        self._palette      = regs.add("palette",      self.PaletteReg(),     offset=0x8)
        self._palette_busy = regs.add("palette_busy", self.PaletteBusyReg(), offset=0xC)

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        m.submodules += self.persist

        connect(m, flipped(self.bus), self._bridge.bus)

        m.d.comb += self.persist.enable.eq(self.en)

        with m.If(self._persist.f.persist.w_stb):
            m.d.sync += self.persist.holdoff.eq(self._persist.f.persist.w_data)

        with m.If(self._decay.f.decay.w_stb):
            m.d.sync += self.persist.decay.eq(self._decay.f.decay.w_data)

        # palette update logic
        palette_busy = Signal()
        m.d.comb += self._palette_busy.f.busy.r_data.eq(palette_busy)

        with m.If(self._palette.element.w_stb & ~palette_busy):
            m.d.sync += [
                palette_busy                            .eq(1),
                self.video.palette_rgb.valid            .eq(1),
                self.video.palette_rgb.payload.position .eq(self._palette.f.position.w_data),
                self.video.palette_rgb.payload.red      .eq(self._palette.f.red.w_data),
                self.video.palette_rgb.payload.green    .eq(self._palette.f.green.w_data),
                self.video.palette_rgb.payload.blue     .eq(self._palette.f.blue.w_data),
            ]

        with m.If(palette_busy & self.video.palette_rgb.ready):
            # coefficient has been written
            m.d.sync += [
                palette_busy.eq(0),
                self.video.palette_rgb.valid.eq(0),
            ]

        return m

class TiliquaSoc(Component):
    def __init__(self, *, firmware_path, dvi_timings, audio_192=False,
                 audio_out_peripheral=True, touch=False, finalize_csr_bridge=True,
                 video_rotate_90=False):

        super().__init__({})

        self.firmware_path = firmware_path
        self.touch = touch
        self.audio_192 = audio_192
        self.dvi_timings = dvi_timings
        self.video_rotate_90 = video_rotate_90

        self.clock_sync_hz = TILIQUA_CLOCK_SYNC_HZ

        self.mainram_base         = 0x00000000
        self.mainram_size         = 0x00008000
        self.psram_base           = 0x20000000
        self.psram_size           = 16*1024*1024
        self.csr_base             = 0xf0000000
        # (gap) leds/gpio0
        self.uart0_base           = 0x00000200
        self.timer0_base          = 0x00000300
        self.timer0_irq           = 0
        self.timer1_base          = 0x00000400
        self.timer1_irq           = 1
        self.i2c0_base            = 0x00000500
        self.encoder0_base        = 0x00000600
        self.pmod0_periph_base    = 0x00000700
        self.dtr0_base            = 0x00000800
        self.video_periph_base    = 0x00000900

        # cpu
        self.cpu = VexRiscv(
            variant="cynthion",
            reset_addr=self.mainram_base
        )

        # interrupt controller
        self.interrupt_controller = InterruptController(width=len(self.cpu.irq_external))

        # bus
        self.wb_arbiter  = wishbone.Arbiter(
            addr_width=30,
            data_width=32,
            granularity=8,
            features={"cti", "bte", "err"}
        )
        self.wb_decoder  = wishbone.Decoder(
            addr_width=30,
            data_width=32,
            granularity=8,
            alignment=0,
            features={"cti", "bte", "err"}
        )

        # mainram
        self.mainram = sram.Peripheral(size=self.mainram_size)
        self.wb_decoder.add(self.mainram.bus, addr=self.mainram_base, name="mainram")

        # csr decoder
        self.csr_decoder = csr.Decoder(addr_width=28, data_width=8)

        # uart0
        uart_baud_rate = 115200
        divisor = int(self.clock_sync_hz // uart_baud_rate)
        self.uart0 = uart.Peripheral(divisor=divisor)
        self.csr_decoder.add(self.uart0.bus, addr=self.uart0_base, name="uart0")

        # FIXME: timer events / isrs currently not implemented, adding the event
        # bus to the csr decoder segfaults yosys somehow ...

        # timer0
        self.timer0 = timer.Peripheral(width=32)
        self.csr_decoder.add(self.timer0.bus, addr=self.timer0_base, name="timer0")
        self.interrupt_controller.add(self.timer0, number=self.timer0_irq, name="timer0")

        # timer1
        self.timer1 = timer.Peripheral(width=32)
        self.csr_decoder.add(self.timer1.bus, addr=self.timer1_base, name="timer1")
        self.interrupt_controller.add(self.timer1, name="timer1", number=self.timer1_irq)

        # psram peripheral
        self.psram_periph = psram_peripheral.Peripheral(size=self.psram_size)
        self.wb_decoder.add(self.psram_periph.bus, addr=self.psram_base, name="psram")

        # video PHY (DMAs from PSRAM starting at fb_base)
        fb_base = self.psram_base
        fb_size = (dvi_timings.h_active, dvi_timings.v_active)
        self.video = video.FramebufferPHY(
                fb_base=fb_base, dvi_timings=dvi_timings, fb_size=fb_size,
                bus_master=self.psram_periph.bus)
        self.psram_periph.add_master(self.video.bus)

        # mobo i2c
        self.i2c0 = i2c.Peripheral(period_cyc=240)
        self.csr_decoder.add(self.i2c0.bus, addr=self.i2c0_base, name="i2c0")

        # encoder
        self.encoder0 = encoder.Peripheral()
        self.csr_decoder.add(self.encoder0.bus, addr=self.encoder0_base, name="encoder0")

        # pmod periph
        self.pmod0_periph = eurorack_pmod_peripheral.Peripheral(
                pmod=None, enable_out=audio_out_peripheral)
        self.csr_decoder.add(self.pmod0_periph.bus, addr=self.pmod0_periph_base, name="pmod0_periph")

        # die temperature
        self.dtr0 = dtr.Peripheral()
        self.csr_decoder.add(self.dtr0.bus, addr=self.dtr0_base, name="dtr0")

        # video persistance effect (all writes gradually fade) -
        # this is an interesting alternative to double-buffering that looks
        # kind of like an old CRT with slow-scanning.
        self.video_periph = VideoPeripheral(
            fb_base=self.video.fb_base,
            fb_size=fb_size,
            bus_dma=self.psram_periph,
            video=self.video)
        self.csr_decoder.add(self.video_periph.bus, addr=self.video_periph_base, name="video_periph")

        self.permit_bus_traffic = Signal()

        if finalize_csr_bridge:
            self.finalize_csr_bridge()

    def finalize_csr_bridge(self):

        # Finalizing the CSR bridge / peripheral memory map may not be desirable in __init__
        # if we want to add more after this class has been instantiated. So it's optional
        # during __init__ but MUST be called once before the design is elaborated.

        self.wb_to_csr = WishboneCSRBridge(self.csr_decoder.bus, data_width=32)
        self.wb_decoder.add(self.wb_to_csr.wb_bus, addr=self.csr_base, sparse=False, name="wb_to_csr")

    def elaborate(self, platform):


        m = Module()

        self.mainram.init = readbin.get_mem_data(self.firmware_path, data_width=32, endianness="little")
        assert self.mainram.init

        # bus
        m.submodules += [self.wb_arbiter, self.wb_decoder]
        wiring.connect(m, self.wb_arbiter.bus, self.wb_decoder.bus)

        # cpu
        m.submodules += self.cpu
        self.wb_arbiter.add(self.cpu.ibus)
        self.wb_arbiter.add(self.cpu.dbus)

        # interrupt controller
        m.submodules += self.interrupt_controller
        # TODO wiring.connect(m, self.cpu.irq_external, self.irqs.pending)
        m.d.comb += self.cpu.irq_external.eq(self.interrupt_controller.pending)

        # mainram
        m.submodules += self.mainram

        # csr decoder
        m.submodules += self.csr_decoder

        # uart0
        m.submodules += self.uart0
        if sim.is_hw(platform):
            uart0_provider = uart.Provider(0)
            m.submodules += uart0_provider
            wiring.connect(m, self.uart0.pins, uart0_provider.pins)

        # timer0
        m.submodules += self.timer0

        # timer1
        m.submodules += self.timer1

        # i2c0
        m.submodules += self.i2c0
        if sim.is_hw(platform):
            i2c0_provider = i2c.Provider()
            m.submodules += i2c0_provider
            wiring.connect(m, self.i2c0.pins, i2c0_provider.pins)

        # encoder0
        m.submodules += self.encoder0
        if sim.is_hw(platform):
            encoder0_provider = encoder.Provider()
            m.submodules += encoder0_provider
            wiring.connect(m, self.encoder0.pins, encoder0_provider.pins)

        # psram
        m.submodules += self.psram_periph

        # video PHY
        m.submodules += self.video

        # video periph / persist
        m.submodules += self.video_periph

        if sim.is_hw(platform):
            # pmod0
            # add a eurorack pmod instance without an audio stream for basic self-testing
            # connect it to our test peripheral before instantiating SoC.
            m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                    pmod_pins=platform.request("audio_ffc"),
                    hardware_r33=True,
                    touch_enabled=self.touch,
                    audio_192=self.audio_192)
            self.pmod0_periph.pmod = pmod0
            m.submodules += self.pmod0_periph

            # die temperature
            m.submodules += self.dtr0

            # generate our domain clocks/resets
            m.submodules.car = platform.clock_domain_generator(audio_192=self.audio_192,
                                                               pixclk_pll=self.dvi_timings.pll)

            # Enable LED driver on motherboard
            m.d.comb += platform.request("mobo_leds_oe").o.eq(1),

            # HACK: encoder push override -- hold for 3sec will re-enter bootloader
            REBOOT_SEC = 3
            button_counter = Signal(unsigned(32))
            with m.If(button_counter > REBOOT_SEC*self.clock_sync_hz):
                m.d.comb += platform.request("self_program").o.eq(1)
            with m.If(self.encoder0._button.f.button.r_data):
                m.d.sync += button_counter.eq(button_counter + 1)
            with m.Else():
                m.d.sync += button_counter.eq(0)
        else:
            m.submodules.car = sim.FakeTiliquaDomainGenerator()

        # wishbone csr bridge
        m.submodules += self.wb_to_csr

        # Memory controller hangs if we start making requests to it straight away.
        on_delay = Signal(32)
        with m.If(on_delay < 0xFF):
            m.d.comb += self.cpu.ext_reset.eq(1)
        with m.If(on_delay < 0xFFFF):
            m.d.sync += on_delay.eq(on_delay+1)
        with m.Else():
            m.d.sync += self.permit_bus_traffic.eq(1)
            m.d.sync += self.video.enable.eq(1)
            m.d.sync += self.video_periph.en.eq(1)

        return m

    def gensvd(self, dst_svd):
        """Generate top-level SVD."""
        print("Generating SVD ...", dst_svd)
        with open(dst_svd, "w") as f:
            GenerateSVD(self).generate(file=f)
        print("Wrote SVD ...", dst_svd)

    def genmem(self, dst_mem):
        """Generate linker regions for Rust (memory.x)."""
        memory_x = (
            "MEMORY {{\n"
            "    mainram : ORIGIN = {mainram_base}, LENGTH = {mainram_size}\n"
            "}}\n"
            "REGION_ALIAS(\"REGION_TEXT\", mainram);\n"
            "REGION_ALIAS(\"REGION_RODATA\", mainram);\n"
            "REGION_ALIAS(\"REGION_DATA\", mainram);\n"
            "REGION_ALIAS(\"REGION_BSS\", mainram);\n"
            "REGION_ALIAS(\"REGION_HEAP\", mainram);\n"
            "REGION_ALIAS(\"REGION_STACK\", mainram);\n"
        )
        print("Generating (rust) memory.x ...", dst_mem)
        with open(dst_mem, "w") as f:
            f.write(memory_x.format(mainram_base=hex(self.mainram_base),
                                    mainram_size=hex(self.mainram.size)))

    def genconst(self, dst):
        """Generate some high-level constants used by application code."""
        # TODO: better to move these to SVD vendor section?
        print("Generating (rust) constants ...", dst)
        with open(dst, "w") as f:
            f.write(f"pub const CLOCK_SYNC_HZ: u32    = {self.clock_sync_hz};\n")
            f.write(f"pub const PSRAM_BASE: usize     = 0x{self.psram_base:x};\n")
            f.write(f"pub const PSRAM_SZ_BYTES: usize = 0x{self.psram_size:x};\n")
            f.write(f"pub const PSRAM_SZ_WORDS: usize = PSRAM_SZ_BYTES / 4;\n")
            f.write(f"pub const H_ACTIVE: u32         = {self.video.fb_hsize};\n")
            f.write(f"pub const V_ACTIVE: u32         = {self.video.fb_vsize};\n")
            f.write(f"pub const VIDEO_ROTATE_90: bool = {'true' if self.video_rotate_90 else 'false'};\n")
            f.write(f"pub const PSRAM_FB_BASE: usize  = 0x{self.video.fb_base:x};\n")
            f.write(f"pub const PX_HUE_MAX: i32       = 16;\n")
            f.write(f"pub const PX_INTENSITY_MAX: i32 = 16;\n")

    def regenerate_pac_from_svd(svd_path):
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

    def compile_firmware(path):
        subprocess.check_call([
            "cargo", "build", "--release"
            ], env=os.environ, cwd=os.path.join(path, "fw"))
        subprocess.check_call([
            "cargo", "objcopy", "--release", "--", "-Obinary", "firmware.bin"
            ], env=os.environ, cwd=os.path.join(path, "fw"))

def top_level_cli(fragment, *pos_args, path, **kwargs):

    os.environ["AMARANTH_debug_verilog"] = "1"
    os.environ["AMARANTH_nextpnr_opts"] = "--timing-allow-fail"
    os.environ["AMARANTH_ecppack_opts"] = "--freq 38.8 --compress"

    # Configure logging.
    logging.getLogger().setLevel(logging.DEBUG)

    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--resolution', type=str, default="1280x720p60",
                        help="DVI resolution - (default: 1280x720p60)")
    parser.add_argument('--rotate-90', action='store_true',
                        help="Rotate DVI out by 90 degrees")
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
    args = parser.parse_args()

    assert args.resolution in video.DVI_TIMINGS, f"error: video resolution must be one of {DVI_TIMINGS.keys()}"
    dvi_timings = video.DVI_TIMINGS[args.resolution]

    if args.rotate_90:
        kwargs["video_rotate_90"] = True

    if issubclass(fragment, TiliquaSoc):
        kwargs["firmware_path"] = os.path.join(path, "fw/firmware.bin")

    name = fragment.__name__ if callable(fragment) else fragment.__class__.__name__
    assert callable(fragment)
    fragment = fragment(*pos_args, dvi_timings=dvi_timings, **kwargs)

    if isinstance(fragment, TiliquaSoc):

        # Generate SVD
        svd_path = os.path.join(path, "fw/soc.svd")
        fragment.gensvd(svd_path)
        if args.svd_only:
            sys.exit(0)

        # (re)-generate PAC (from SVD)
        TiliquaSoc.regenerate_pac_from_svd(svd_path)
        if args.pac_only:
            sys.exit(0)

        # Generate memory.x and some extra constants
        # Finally, build our stripped firmware image.
        fragment.genmem(os.path.join(path, "fw/memory.x"))
        fragment.genconst("src/rs/lib/src/generated_constants.rs")
        TiliquaSoc.compile_firmware(path)
        if args.fw_only:
            sys.exit(0)

    if args.sim:
        sim.simulate_soc(fragment, args.trace_fst)
        sys.exit(0)

    if args.sc3:
        platform = TiliquaR2SC3Platform()
    else:
        platform = TiliquaR2SC2Platform()

    print("Building bitstream for", platform.name)
    platform.build(fragment)

    return fragment
