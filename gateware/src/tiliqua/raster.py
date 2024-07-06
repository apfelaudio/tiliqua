# Utilities and effects for rasterizing information to a framebuffer.
#
# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import colorsys
import os

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out
from amaranth.lib.fifo     import AsyncFIFO, SyncFIFO
from amaranth.lib.cdc      import FFSynchronizer
from amaranth.utils        import log2_int
from amaranth.hdl.mem      import Memory

from amaranth_future       import stream, fixed


from tiliqua               import dsp
from tiliqua.eurorack_pmod import ASQ

from amaranth_soc          import wishbone

class Persistance(Elaboratable):

    """
    Read pixels from a framebuffer in PSRAM and apply gradual intensity reduction to simulate oscilloscope glow.
    Pixels are DMA'd from PSRAM as a wishbone master in bursts of 'fifo_depth // 2' in the 'sync' clock domain.
    The block of pixels has its intensity reduced and is then DMA'd back to the bus.

    'holdoff' is used to keep this core from saturating the bus between bursts.
    """

    def __init__(self, *, fb_base, bus_master, fb_size,
                 fifo_depth=128, holdoff_default=1024, fb_bytes_per_pixel=1):
        super().__init__()

        self.fb_base = fb_base
        self.fb_hsize, self.fb_vsize = fb_size
        self.fifo_depth = fifo_depth
        self.fb_bytes_per_pixel = fb_bytes_per_pixel

        # Tweakables
        self.holdoff = Signal(16, reset=holdoff_default)
        self.decay   = Signal(4, reset=1)

        # We are a DMA master
        self.bus = wishbone.Interface(addr_width=bus_master.addr_width, data_width=32, granularity=8,
                                      features={"cti", "bte"})

        # FIFO to cache pixels from PSRAM.
        self.fifo = SyncFIFO(width=32, depth=fifo_depth)

        # Current addresses in the framebuffer (read and write sides)
        self.dma_addr_in = Signal(32, reset=0)
        self.dma_addr_out = Signal(32)

        # Kick to start this core.
        self.enable = Signal(1, reset=0)

    def elaborate(self, platform) -> Module:
        m = Module()

        # Length of framebuffer in 32-bit words
        fb_len_words = (self.fb_bytes_per_pixel * (self.fb_hsize*self.fb_vsize)) // 4

        holdoff_count = Signal(32)
        pnext = Signal(32)
        wr_source = Signal(32)

        m.submodules.fifo = self.fifo
        bus = self.bus
        dma_addr_in = self.dma_addr_in
        dma_addr_out = self.dma_addr_out

        decay_latch = Signal(4)

        # Persistance state machine in 'sync' domain.
        with m.FSM() as fsm:
            with m.State('OFF'):
                with m.If(self.enable):
                    m.next = 'BURST-IN'

            with m.State('BURST-IN'):
                m.d.sync += holdoff_count.eq(0)
                m.d.sync += decay_latch.eq(self.decay)
                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(0),
                    bus.sel.eq(2**(bus.data_width//8)-1),
                    bus.adr.eq(self.fb_base + dma_addr_in),
                ]
                with m.If(~self.fifo.w_rdy):
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.END_OF_BURST)
                    m.next = 'WAIT1'
                with m.Else():
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.INCR_BURST)
                with m.If(bus.stb & bus.ack & self.fifo.w_rdy): # WARN: drops last word
                    m.d.comb += self.fifo.w_en.eq(1)
                    m.d.comb += self.fifo.w_data.eq(bus.dat_r),
                    with m.If(dma_addr_in < (fb_len_words-1)):
                        m.d.sync += dma_addr_in.eq(dma_addr_in + 1)
                    with m.Else():
                        m.d.sync += dma_addr_in.eq(0)

            with m.State('WAIT1'):
                m.d.sync += holdoff_count.eq(holdoff_count + 1)
                with m.If(holdoff_count > self.holdoff):
                    m.d.sync += pnext.eq(self.fifo.r_data)
                    m.d.comb += self.fifo.r_en.eq(1)
                    m.next = 'BURST-OUT'

            with m.State('BURST-OUT'):
                m.d.sync += holdoff_count.eq(0)

                # Incoming pixel array (read from FIFO)
                pixa = Signal(data.ArrayLayout(unsigned(8), 4))
                # Outgoing pixel array (write to bus)
                pixb = Signal(data.ArrayLayout(unsigned(8), 4))

                m.d.comb += [
                    pixa.eq(wr_source),
                ]

                # The actual persistance calculation. 4 pixels at a time.
                for n in range(4):
                    # color
                    m.d.comb += pixb[n][0:4].eq(pixa[n][0:4])
                    # intensity
                    with m.If(pixa[n][4:8] >= decay_latch):
                        m.d.comb += pixb[n][4:8].eq(pixa[n][4:8] - decay_latch)
                    with m.Else():
                        m.d.comb += pixb[n][4:8].eq(0)


                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(1),
                    bus.sel.eq(2**(bus.data_width//8)-1),
                    bus.dat_w.eq(pixb),
                    bus.adr.eq(self.fb_base + dma_addr_out),
                    wr_source.eq(pnext),
                ]

                with m.If(~self.fifo.r_rdy):
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.END_OF_BURST)
                    m.next = 'WAIT2'
                with m.Else():
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.INCR_BURST)
                with m.If(bus.stb & bus.ack):
                    m.d.comb += self.fifo.r_en.eq(1)
                    m.d.comb += wr_source.eq(self.fifo.r_data),
                    with m.If(dma_addr_out < (fb_len_words-1)):
                        m.d.sync += dma_addr_out.eq(dma_addr_out + 1)
                        m.d.comb += bus.adr.eq(self.fb_base + dma_addr_out + 1),
                    with m.Else():
                        m.d.sync += dma_addr_out.eq(0)
                        m.d.comb += bus.adr.eq(self.fb_base + 0),

            with m.State('WAIT2'):
                m.d.sync += holdoff_count.eq(holdoff_count + 1)
                with m.If(holdoff_count > self.holdoff):
                    m.next = 'BURST-IN'

        return m

class Stroke(wiring.Component):

    """
    Read samples, upsample them, and draw to a framebuffer.
    Pixels are DMA'd to PSRAM as a wishbone master, NOT in bursts, as we have no idea
    where each pixel is going to land beforehand. This is the most expensive use of
    PSRAM time in this project as we spend ages waiting on memory latency.

    TODO: can we somehow cache bursts of pixels here?

    Each pixel must be read before we write it for 2 reasons:
    - We have 4 pixels per word, so we can't just write 1 pixel as it would erase the
      adjacent ones.
    - We don't just set max intensity on drawing a pixel, rather we read the current
      intensity and add to it. Otherwise, we get no intensity gradient and the display
      looks nowhere near as nice :)

    To obtain more points, the pixels themselves are upsampled using an FIR-based
    fractional resampler. This is kind of analogous to sin(x)/x interpolation.
    """

    # x, y, intensity, color
    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def __init__(self, *, fb_base, bus_master, fb_size, fb_bytes_per_pixel=1, fs=192000, n_upsample=4,
                 default_hue=10, default_x=0, default_y=0, video_rotate_90=False):


        # FIXME: move this further up chain, collapse env variables
        self.rotate_90 = True if os.getenv("TILIQUA_VIDEO_ROTATE") == "1" else False

        self.fb_base = fb_base
        self.fb_hsize, self.fb_vsize = fb_size
        self.fb_bytes_per_pixel = fb_bytes_per_pixel
        self.fs = fs
        self.n_upsample = n_upsample

        self.bus = wishbone.Interface(addr_width=bus_master.addr_width, data_width=32, granularity=8,
                                      features={"cti", "bte"})

        self.sample_x = Signal(signed(16))
        self.sample_y = Signal(signed(16))
        self.sample_p = Signal(signed(16)) # intensity modulation TODO
        self.sample_c = Signal(signed(16)) # color modulation DONE

        self.hue       = Signal(4, reset=default_hue);
        self.intensity = Signal(4, reset=8);
        self.scale_x   = Signal(4, reset=6);
        self.scale_y   = Signal(4, reset=6);
        self.x_offset  = Signal(signed(16), reset=default_x)
        self.y_offset  = Signal(signed(16), reset=default_y)

        self.px_read = Signal(32)
        self.px_sum = Signal(16)

        # Kick this to start the core
        self.enable = Signal(1, reset=0)

        super().__init__()


    def elaborate(self, platform) -> Module:
        m = Module()

        bus = self.bus

        fb_len_words = (self.fb_hsize*self.fb_vsize) // 4

        sample_x = self.sample_x
        sample_y = self.sample_y
        sample_p = self.sample_p
        sample_c = self.sample_c

        point_stream = None
        if self.n_upsample is not None and self.n_upsample != 1:
            # If interpolation is enabled, insert an FIR upsampling stage.
            m.submodules.split = split = dsp.Split(n_channels=4)
            m.submodules.merge = merge = dsp.Merge(n_channels=4)

            m.submodules.resample0 = resample0 = dsp.Resample(fs_in=self.fs, n_up=self.n_upsample, m_down=1)
            m.submodules.resample1 = resample1 = dsp.Resample(fs_in=self.fs, n_up=self.n_upsample, m_down=1)
            m.submodules.resample2 = resample2 = dsp.Resample(fs_in=self.fs, n_up=self.n_upsample, m_down=1)
            m.submodules.resample3 = resample3 = dsp.Resample(fs_in=self.fs, n_up=self.n_upsample, m_down=1)

            wiring.connect(m, wiring.flipped(self.i), split.i)

            wiring.connect(m, split.o[0], resample0.i)
            wiring.connect(m, split.o[1], resample1.i)
            wiring.connect(m, split.o[2], resample2.i)
            wiring.connect(m, split.o[3], resample3.i)

            wiring.connect(m, resample0.o, merge.i[0])
            wiring.connect(m, resample1.o, merge.i[1])
            wiring.connect(m, resample2.o, merge.i[2])
            wiring.connect(m, resample3.o, merge.i[3])

            point_stream=merge.o
        else:
            point_stream=self.i

        px_read = self.px_read
        px_sum = self.px_sum

        sample_intensity = Signal(4)

        # pixel position
        fb_hwords = ((self.fb_hsize*self.fb_bytes_per_pixel)//4)
        x_offs = Signal(unsigned(16))
        y_offs = Signal(unsigned(16))
        subpix_shift = Signal(unsigned(6))
        pixel_offs = Signal(unsigned(32))

        m.d.comb += pixel_offs.eq(y_offs*fb_hwords + x_offs),
        if self.rotate_90:
            # remap pixel offset for 90deg rotation
            m.d.comb += [
                subpix_shift.eq((-sample_y)[0:2]*8),
                x_offs.eq((fb_hwords//2) + ((-sample_y)>>2)),
                y_offs.eq(sample_x + (self.fb_vsize//2)),
            ]
        else:
            m.d.comb += [
                subpix_shift.eq(sample_x[0:2]*8),
                x_offs.eq((fb_hwords//2) + (sample_x>>2)),
                y_offs.eq(sample_y + (self.fb_vsize//2)),
            ]

        with m.FSM() as fsm:

            with m.State('OFF'):
                with m.If(self.enable):
                    m.next = 'LATCH0'

            with m.State('LATCH0'):

                m.d.comb += point_stream.ready.eq(1)
                # Fired on every audio sample fs_strobe
                with m.If(point_stream.valid):
                    m.d.sync += [
                        sample_x.eq((point_stream.payload[0].sas_value()>>self.scale_x) + self.x_offset),
                        sample_y.eq((point_stream.payload[1].sas_value()>>self.scale_y) + self.y_offset),
                        sample_p.eq(point_stream.payload[2].sas_value()),
                        sample_c.eq(point_stream.payload[3].sas_value()),
                        sample_intensity.eq(self.intensity),
                    ]
                    m.next = 'LATCH1'

            with m.State('LATCH1'):

                with m.If((x_offs < fb_hwords) & (y_offs < self.fb_vsize)):
                    m.d.sync += [
                        bus.sel.eq(0xf),
                        bus.adr.eq(self.fb_base + pixel_offs),
                    ]
                    m.next = 'READ'
                with m.Else():
                    # don't draw outside the screen boundaries
                    m.next = 'LATCH0'

            with m.State('READ'):

                m.d.comb += [
                    bus.cti.eq(wishbone.CycleType.CLASSIC),
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(0),
                ]

                with m.If(bus.stb & bus.ack):
                    m.d.sync += px_read.eq(bus.dat_r)
                    m.d.sync += px_sum.eq(((bus.dat_r >> subpix_shift) & 0xff))
                    m.next = 'WAIT'

            with m.State('WAIT'):
                m.next = 'WAIT2'

            with m.State('WAIT2'):
                m.next = 'WAIT3'

            with m.State('WAIT3'):
                m.next = 'WRITE'

            with m.State('WRITE'):

                m.d.comb += [
                    bus.cti.eq(wishbone.CycleType.CLASSIC),
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(1),
                ]

                # The actual drawing logic
                # Basically we just increment the intensity and clamp it to a maximum
                # for the correct bits of the native bus word for this pixel.
                #
                # TODO: color is always overridden, perhaps we should mix it?

                new_color = Signal(unsigned(4))
                white = Signal(unsigned(4))
                m.d.comb += white.eq(0xf)
                m.d.comb += new_color.eq((sample_c>>10) + self.hue)

                with m.If(px_sum[4:8] + sample_intensity >= 0xF):
                    m.d.comb += bus.dat_w.eq(
                        (px_read & ~(Const(0xFF, unsigned(32)) << subpix_shift)) |
                        (Cat(new_color, white) << (subpix_shift))
                         )
                with m.Else():
                    m.d.comb += bus.dat_w.eq(
                        (px_read & ~(Const(0xFF, unsigned(32)) << subpix_shift)) |
                        (Cat(new_color, (px_sum[4:8] + sample_intensity)) << subpix_shift)
                         )

                with m.If(bus.stb & bus.ack):
                    m.next = 'LATCH0'

        return m

