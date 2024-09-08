# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# Based on some work from LUNA project licensed under BSD. Anything new
# in this file is issued under the following license:
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import argparse
import logging
import os
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

from tiliqua.tiliqua_platform                    import TiliquaPlatform

from tiliqua                                     import psram_peripheral, i2c, encoder, dtr, video, eurorack_pmod_peripheral
from tiliqua                                     import eurorack_pmod

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

class SimPlatform():
    def __init__(self):
        self.files = {}
        pass
    def add_file(self, file_name, contents):
        self.files[file_name] = contents

class TiliquaSoc(Component):
    def __init__(self, *, firmware_path, dvi_timings, audio_192=False,
                 audio_out_peripheral=True, touch=False, finalize_csr_bridge=True):

        super().__init__({})

        self.firmware_path = firmware_path
        self.touch = touch
        self.audio_192 = audio_192
        self.dvi_timings = dvi_timings
        # FIXME move somewhere more obvious
        self.video_rotate_90 = True if os.getenv("TILIQUA_VIDEO_ROTATE") == "1" else False

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
                bus_master=self.psram_periph.bus, sim=False)
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
        if not isinstance(platform, SimPlatform):
            uart0_provider = uart.Provider(0)
            m.submodules += uart0_provider
            wiring.connect(m, self.uart0.pins, uart0_provider.pins)

        # timer0
        m.submodules += self.timer0

        # timer1
        m.submodules += self.timer1

        # i2c0
        m.submodules += self.i2c0
        if not isinstance(platform, SimPlatform):
            i2c0_provider = i2c.Provider()
            m.submodules += i2c0_provider
            wiring.connect(m, self.i2c0.pins, i2c0_provider.pins)

        # encoder0
        m.submodules += self.encoder0
        if not isinstance(platform, SimPlatform):
            encoder0_provider = encoder.Provider()
            m.submodules += encoder0_provider
            wiring.connect(m, self.encoder0.pins, encoder0_provider.pins)

        if not isinstance(platform, SimPlatform):

            # psram
            m.submodules += self.psram_periph

            # video PHY
            m.submodules += self.video

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

            # video periph / persist
            m.submodules += self.video_periph

            # generate our domain clocks/resets
            m.submodules.car = platform.clock_domain_generator(audio_192=self.audio_192,
                                                               pixclk_pll=self.dvi_timings.pll)

            # Enable LED driver on motherboard
            m.d.comb += platform.request("mobo_leds_oe").o.eq(1),

            # HACK: encoder push override -- hold for 3sec will re-enter bootloader
            REBOOT_SEC = 3
            button_counter = Signal(unsigned(32))
            with m.If(button_counter > REBOOT_SEC*self.clk_sync_hz):
                m.d.comb += platform.request("self_program").o.eq(1)
            with m.If(self.encoder0._button.f.button.r_data):
                m.d.sync += button_counter.eq(button_counter + 1)
            with m.Else():
                m.d.sync += button_counter.eq(0)

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

    def genrust_constants(self, dst):
        # TODO: move these to SVD vendor section
        print("writing", dst)
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

def sim(fragment, tracing=False):
    import subprocess
    from amaranth.back import verilog

    build_dst = "build"
    dst = f"{build_dst}/tiliqua_soc.v"
    print(f"write verilog implementation of 'tiliqua_soc' to '{dst}'...")

    # Main purpose of using this custom platform instead of
    # simply None is to track extra files added to the build.
    sim_platform = SimPlatform()

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(verilog.convert(
            fragment,
            platform=sim_platform,
            ports=[
                fragment.uart0._tx_data.f.data.w_data,
                fragment.uart0._tx_data.f.data.w_stb,
            ]))

    # Write all additional files added with platform.add_file()
    # to build/ directory, so verilator build can find them.
    for file in sim_platform.files:
        with open(os.path.join("build", file), "w") as f:
            f.write(sim_platform.files[file])

    tracing_flags = ["--trace-fst", "--trace-structs"] if tracing else []

    verilator_dst = "build/obj_dir"
    print(f"verilate '{dst}' into C++ binary...")
    subprocess.check_call(["verilator",
                           "-Wno-COMBDLY",
                           "-Wno-CASEINCOMPLETE",
                           "-Wno-CASEOVERLAP",
                           "-Wno-WIDTHEXPAND",
                           "-Wno-WIDTHTRUNC",
                           "-Wno-TIMESCALEMOD",
                           "-Wno-PINMISSING",
                           "-Wno-ASCRANGE",
                           "-cc"] + tracing_flags + [
                           "--exe",
                           "--Mdir", f"{verilator_dst}",
                           "--build",
                           "-j", "0",
                           "-Ibuild",
                           "-CFLAGS", f"-DSYNC_CLK_HZ={fragment.clock_sync_hz}",
                           "../../src/selftest/sim.cpp",
                           f"{dst}",
                           ] + [
                               f for f in sim_platform.files
                               if f.endswith(".svh") or f.endswith(".sv") or f.endswith(".v")
                           ],
                          env=os.environ)

    print(f"run verilated binary '{verilator_dst}/Vtiliqua_soc'...")
    subprocess.check_call([f"{verilator_dst}/Vtiliqua_soc"],
                          env=os.environ)

    print(f"done.")

memory_x = """MEMORY {{
    mainram : ORIGIN = {mainram_base}, LENGTH = {mainram_size}
}}
REGION_ALIAS("REGION_TEXT", mainram);
REGION_ALIAS("REGION_RODATA", mainram);
REGION_ALIAS("REGION_DATA", mainram);
REGION_ALIAS("REGION_BSS", mainram);
REGION_ALIAS("REGION_HEAP", mainram);
REGION_ALIAS("REGION_STACK", mainram);
"""

def top_level_cli(fragment, *pos_args, **kwargs):

    # Configure logging.
    logging.getLogger().setLevel(logging.DEBUG)

    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--genrust', action='store_true',
        help="If provided, artifacts needed to build Rust firmware are generated. Bitstream is not built")

    parser.add_argument('--sim', action='store_true')
    parser.add_argument('--trace-fst', action='store_true')
    args = parser.parse_args()

    # If this isn't a fragment directly, interpret it as an object that will build one.
    name = fragment.__name__ if callable(fragment) else fragment.__class__.__name__
    if callable(fragment):
        fragment = fragment(*pos_args, **kwargs)

    if args.genrust:
        # FIXME: put these in SVD?
        fragment.genrust_constants("src/rs/lib/src/generated_constants.rs")

        # Generate top-level SVD
        dst_svd = "build/soc.svd"
        print("generating", dst_svd)
        with open(dst_svd, "w") as f:
            GenerateSVD(fragment).generate(file=f)

        # Generate linker regions
        dst_mem = "build/memory.x"
        print("generating", dst_mem)
        with open(dst_mem, "w") as f:
            f.write(memory_x.format(mainram_base=hex(fragment.mainram_base),
                                    mainram_size=hex(fragment.mainram.size)))

        sys.exit(0)

    if args.sim:
        sim(fragment, args.trace_fst)
        sys.exit(0)

    TiliquaPlatform().build(fragment)

    return fragment
