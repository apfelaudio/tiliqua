# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Amaranth wrapper and clock domain crossing for `eurorack-pmod` hardware."""

import os

from amaranth                   import *
from amaranth.build             import *
from amaranth.lib               import wiring, data, stream
from amaranth.lib.wiring        import In, Out
from amaranth.lib.fifo          import AsyncFIFOBuffered
from amaranth.lib.cdc           import FFSynchronizer
from amaranth.lib.memory   import Memory

from tiliqua                    import i2c
from vendor                     import i2c as vendor_i2c

from amaranth_future            import fixed

WIDTH = 16

# Native 'Audio sample SQ', shape of audio samples from CODEC.
ASQ = fixed.SQ(0, WIDTH-1)

class AudioStream(wiring.Component):

    """
    Domain crossing logic to move samples from `eurorack-pmod` logic in the audio domain
    to logic in a different (faster) domain using a stream interface.
    This is used by most DSP examples for glitch-free audio streaming.
    """

    istream: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    ostream: In(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def __init__(self, eurorack_pmod, stream_domain="sync", fifo_depth=8):

        self.eurorack_pmod = eurorack_pmod
        self.stream_domain = stream_domain
        self.fifo_depth = fifo_depth

        super().__init__()

    def elaborate(self, platform) -> Module:

        m = Module()

        m.submodules.adc_fifo = adc_fifo = AsyncFIFOBuffered(
                width=self.eurorack_pmod.sample_i.shape().size, depth=self.fifo_depth,
                w_domain="audio", r_domain=self.stream_domain)
        m.submodules.dac_fifo = dac_fifo = AsyncFIFOBuffered(
                width=self.eurorack_pmod.sample_o.shape().size, depth=self.fifo_depth,
                w_domain=self.stream_domain, r_domain="audio")

        wiring.connect(m, adc_fifo.r_stream, wiring.flipped(self.istream))
        wiring.connect(m, wiring.flipped(self.ostream), dac_fifo.w_stream)

        eurorack_pmod = self.eurorack_pmod

        # below is synchronous logic in the *audio domain*

        # On every fs_strobe, latch and write all channels concatenated
        # into one entry of adc_fifo.

        m.d.audio += [
            # WARN: ignoring rdy in write domain. Mostly fine as long as
            # stream_domain is faster than audio_domain.
            adc_fifo.w_en.eq(eurorack_pmod.fs_strobe),
            adc_fifo.w_data.eq(self.eurorack_pmod.sample_i),
        ]


        # Once fs_strobe hits, write the next pending samples to CODEC

        with m.FSM(domain="audio") as fsm:
            with m.State('READ'):
                with m.If(eurorack_pmod.fs_strobe & dac_fifo.r_rdy):
                    m.d.audio += dac_fifo.r_en.eq(1)
                    m.next = 'SEND'
            with m.State('SEND'):
                m.d.audio += [
                    dac_fifo.r_en.eq(0),
                    self.eurorack_pmod.sample_o.eq(dac_fifo.r_data),
                ]
                m.next = 'READ'

        return m

# TODO: move this to another util lib
class EdgeToPulse(Elaboratable):
    """
    each rising edge of the signal edge_in will be
    converted to a single clock pulse on pulse_out
    """
    def __init__(self):
        self.edge_in          = Signal()
        self.pulse_out        = Signal()

    def elaborate(self, platform) -> Module:
        m = Module()

        edge_last = Signal()

        m.d.sync += edge_last.eq(self.edge_in)
        with m.If(self.edge_in & ~edge_last):
            m.d.comb += self.pulse_out.eq(1)
        with m.Else():
            m.d.comb += self.pulse_out.eq(0)

        return m

class I2CMaster(wiring.Component):

    PCA9557_ADDR     = 0x18
    PCA9635_ADDR     = 0x5
    AK4619VN_ADDR    = 0x10
    CY8CMBR3108_ADDR = 0x37

    N_JACKS   = 8
    N_LEDS    = N_JACKS * 2
    N_SENSORS = 8

    PCA9635_CFG = [
        0x80, # Auto-increment starting from MODE1
        0x81, # MODE1
        0x01, # MODE2
        0x10, # PWM0
        0x10, # PWM1
        0x10, # PWM2
        0x10, # PWM3
        0x10, # PWM4
        0x10, # PWM5
        0x10, # PWM6
        0x10, # PWM7
        0x10, # PWM8
        0x10, # PWM9
        0x10, # PWM10
        0x10, # PWM11
        0x10, # PWM12
        0x10, # PWM13
        0x10, # PWM14
        0x10, # PWM15
        0xFF, # GRPPWM
        0x00, # GRPFREQ
        0xAA, # LEDOUT0
        0xAA, # LEDOUT1
        0xAA, # LEDOUT2
        0xAA, # LEDOUT3
    ]

    AK4619VN_CFG = [
        0x00, # Register address to start at.
        0x37, # 0x00 Power Management
        0xAE, # 0x01 Audio I/F Format
        0x1C, # 0x02 Audio I/F Format
        0x00, # 0x03 System Clock Setting
        0x22, # 0x04 MIC AMP Gain
        0x22, # 0x05 MIC AMP Gain
        0x30, # 0x06 ADC1 Lch Digital Volume
        0x30, # 0x07 ADC1 Rch Digital Volume
        0x30, # 0x08 ADC2 Lch Digital Volume
        0x30, # 0x09 ADC2 Rch Digital Volume
        0x22, # 0x0A ADC Digital Filter Setting
        0x55, # 0x0B ADC Analog Input Setting
        0x00, # 0x0C Reserved
        0x06, # 0x0D ADC Mute & HPF Control
        0x18, # 0x0E DAC1 Lch Digital Volume
        0x18, # 0x0F DAC1 Rch Digital Volume
        0x18, # 0x10 DAC2 Lch Digital Volume
        0x18, # 0x11 DAC2 Rch Digital Volume
        0x04, # 0x12 DAC Input Select Setting
        0x05, # 0x13 DAC De-Emphasis Setting
        0x0A, # 0x14 DAC Mute & Filter Setting
    ]

    def __init__(self):
        self.i2c_stream = i2c.I2CStreamer(period_cyc=256)
        super().__init__({
            "pins":   wiring.Out(vendor_i2c.I2CPinSignature()),
            "jack":   wiring.Out(self.N_JACKS),
            "led":    wiring.In(signed(8)).array(self.N_JACKS),
            "touch":  wiring.Out(unsigned(8)).array(self.N_SENSORS),
        })

    def elaborate(self, platform):
        m = Module()

        m.submodules.i2c_stream = i2c = self.i2c_stream
        wiring.connect(m, wiring.flipped(self.pins), self.i2c_stream.pins)

        def state_id(ix):
            return (f"i2c_state{ix}", f"i2c_state{ix+1}", ix+1)

        def i2c_addr(m, ix, addr):
            cur, nxt, ix = state_id(ix)
            with m.State(cur):
                m.d.sync += i2c.address.eq(addr),
                m.next = nxt
            return cur, nxt, ix

        def i2c_write(m, ix, data, last=False):
            cur, nxt, ix = state_id(ix)
            with m.State(cur):
                m.d.comb += [
                    i2c.i.valid.eq(1),
                    i2c.i.payload.rw.eq(0), # Write
                    i2c.i.payload.data.eq(data),
                    i2c.i.payload.last.eq(1 if last else 0),
                ]
                m.next = nxt
            return cur, nxt, ix

        def i2c_w_arr(m, ix, data):
            cur, nxt, ix = state_id(ix)
            with m.State(cur):
                cnt = Signal(range(len(data)+2))
                mem = Memory(
                    shape=unsigned(8), depth=len(data), init=data)
                m.submodules += mem
                rd_port = mem.read_port()
                m.d.comb += [
                    rd_port.en.eq(1),
                    rd_port.addr.eq(cnt),
                ]
                m.d.sync += cnt.eq(cnt+1)
                with m.If(cnt != len(data) + 1):
                    m.d.comb += [
                        i2c.i.valid.eq(cnt != 0),
                        i2c.i.payload.rw.eq(0), # Write
                        i2c.i.payload.data.eq(rd_port.data),
                        i2c.i.payload.last.eq(cnt == (len(data)-1)),
                    ]
                with m.Else():
                    m.d.sync += cnt.eq(0)
                    m.next = nxt
            return cur, nxt, ix

        def i2c_read(m, ix, last=False):
            cur, nxt, ix = state_id(ix)
            with m.State(cur):
                m.d.comb += [
                    i2c.i.valid.eq(1),
                    i2c.i.payload.rw.eq(1), # Read
                    i2c.i.payload.last.eq(1 if last else 0),
                ]
                m.next = nxt
            return cur, nxt, ix

        def i2c_wait(m, ix):
            cur,  nxt, ix = state_id(ix)
            with m.State(cur):
                with m.If(~i2c.status.busy):
                    m.next = nxt
            return cur, nxt, ix


        # used for implicit state machine ID tracking / generation
        ix = 0

        led_reg = Signal(data.ArrayLayout(unsigned(8), self.N_LEDS))
        for n in range(self.N_LEDS):
            if n % 2 == 0:
                with m.If(self.led[n//2] > 0):
                    m.d.comb += led_reg[n].eq(0)
                with m.Else():
                    m.d.comb += led_reg[n].eq(-self.led[n//2])
            else:
                with m.If(self.led[n//2] > 0):
                    m.d.comb += led_reg[n].eq(self.led[n//2])
                with m.Else():
                    m.d.comb += led_reg[n].eq(0)

        touch_nsensor = Signal(range(self.N_SENSORS))

        startup_delay = Signal(32)

        with m.FSM(init='STARTUP-DELAY') as fsm:

            #
            # CY8CMBR3108 init (SW_RESET)
            # Config is not flashed here, it is assumed touch config
            # in CY8CMBR3108 NVM is already flashed
            #
            # TODO: verify this against checksum read?
            #

            init, _,   ix  = i2c_addr (m, ix, self.CY8CMBR3108_ADDR)
            _,    _,   ix  = i2c_write(m, ix, 0x86)
            _,    _,   ix  = i2c_write(m, ix, 0xff, last=True)
            _,    _,   ix  = i2c_wait (m, ix)

            #
            # startup delay
            #

            with m.State('STARTUP-DELAY'):
                with m.If(startup_delay == 600_000):
                    m.next = init
                with m.Else():
                    m.d.sync += startup_delay.eq(startup_delay+1)

            #
            # CYMBR init retries
            #

            cur, nxt, ix = state_id(ix)
            with m.State(cur):
                with m.If(i2c.status.error):
                    m.next = init
                with m.Else():
                    m.next = nxt

            #
            # AK4619VN init
            #
            _,   _,   ix  = i2c_addr (m, ix, self.AK4619VN_ADDR)
            _,   _,   ix  = i2c_w_arr(m, ix, self.AK4619VN_CFG)
            _,   _,   ix  = i2c_wait (m, ix)

            #
            # PCA9557 init
            #

            _,   _,   ix  = i2c_addr (m, ix, self.PCA9557_ADDR)
            _,   _,   ix  = i2c_write(m, ix, 0x02)
            _,   _,   ix  = i2c_write(m, ix, 0x00, last=True)
            _,   _,   ix  = i2c_wait (m, ix) # set polarity inversion reg

            #
            # PCA9635 init
            #
            _,   _,   ix  = i2c_addr (m, ix, self.PCA9635_ADDR)
            _,   _,   ix  = i2c_w_arr(m, ix, self.PCA9635_CFG)
            _,   _,   ix  = i2c_wait (m, ix)

            #
            # BEGIN MAIN LOOP
            #

            #
            # PCA9635 update (LED brightnesses)
            #
            cur, _,   ix  = i2c_addr (m, ix, self.PCA9635_ADDR)
            _,   _,   ix  = i2c_write(m, ix, 0x82) # start from first brightness reg
            for n in range(self.N_LEDS):
                _,   _,   ix  = i2c_write(m, ix, led_reg[n], last=(n==self.N_LEDS-1))
            _,   _,   ix  = i2c_wait (m, ix)

            s_loop_begin = cur

            #
            # CY8CMBR3108 read (Touch scan registers)
            #

            _,   _,   ix  = i2c_addr (m, ix, self.CY8CMBR3108_ADDR)
            _,   _,   ix  = i2c_write(m, ix, 0xBA + (touch_nsensor<<1))
            _,   _,   ix  = i2c_read (m, ix, last=True)
            _,   _,   ix  = i2c_wait (m, ix)

            # Latch valid reads to dedicated touch register.
            cur, nxt, ix = state_id(ix)
            with m.State(cur):
                m.d.sync += touch_nsensor.eq(touch_nsensor+1)
                with m.If(~i2c.status.error):
                    with m.Switch(touch_nsensor):
                        for n in range(8):
                            if n > 3:
                                # R3.3 hw swaps last four vs R3.2 to improve PCB routing
                                with m.Case(n):
                                    m.d.sync += self.touch[4+(7-n)].eq(i2c.o.payload)
                            else:
                                with m.Case(n):
                                    m.d.sync += self.touch[n].eq(i2c.o.payload)
                    m.d.comb += i2c.o.ready.eq(1)
                m.next = nxt

            #
            # PCA9557 read (Jack input port register)
            #
            _,   _,   ix  = i2c_addr (m, ix, self.PCA9557_ADDR)
            _,   _,   ix  = i2c_write(m, ix, 0x00)
            _,   _,   ix  = i2c_read (m, ix, last=True)
            _,   nxt, ix  = i2c_wait (m, ix)

            # Latch valid reads to dedicated jack register.
            with m.State(nxt):
                with m.If(~i2c.status.error):
                    m.d.sync += self.jack.eq(i2c.o.payload)
                    m.d.comb += i2c.o.ready.eq(1)
                # Go back to LED brightness update
                m.next = s_loop_begin

        return m

class EurorackPmod(wiring.Component):
    """
    Amaranth wrapper for Verilog files from `eurorack-pmod` project.

    Requires an "audio" clock domain running at 12.288MHz (256*Fs).

    There are some Amaranth I2S cores around, however they seem to
    use oversampling, which can be glitchy at such high bit clock
    rates (as needed for 4x4 TDM the AK4619 requires).
    """

    # Output strobe once per sample in the `audio` domain (256*Fs)
    fs_strobe: Out(1)

    # Audio samples latched on `fs_strobe`.
    sample_i: Out(data.ArrayLayout(ASQ, 4))
    sample_o: In(data.ArrayLayout(ASQ, 4))

    # Touch sensing and jacksense outputs.
    touch: Out(8).array(8)
    jack: Out(8)

    # TODO
    # Read from the onboard I2C eeprom.
    # These will be valid a few hundred milliseconds after boot.
    eeprom_mfg: Out(8)
    eeprom_dev: Out(8)
    eeprom_serial: Out(32)

    # TODO
    # Bitwise manual LED overrides. 1 == audio passthrough, 0 == manual set.
    led_mode: In(8, init=0xff)
    # If an LED is in manual, this is signed i8 from -green to +red
    led: In(8).array(8)

    # TODO
    # Signals only used for calibration
    sample_adc: Out(signed(WIDTH)).array(4)
    force_dac_output: In(signed(WIDTH))

    def __init__(self, pmod_pins, hardware_r33=True, touch_enabled=True, audio_192=False):

        self.pmod_pins = pmod_pins
        self.audio_192 = audio_192

        super().__init__()

    def add_verilog_sources(self, platform):

        #
        # Verilog sources from `eurorack-pmod` project.
        #
        # Assumes `eurorack-pmod` repo is checked out in this directory and
        # `git submodule update --init` has been run!
        #

        vroot = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             "../../deps/eurorack-pmod/gateware")

        platform.add_file("eurorack_pmod_defines.sv",
                          f"`define HW_R33\n")
        platform.add_file("cal/cal_mem_default_r33.hex",
                          open(os.path.join(vroot, "cal/cal_mem_default_r33.hex")))

        # Verilog implementation
        platform.add_file("ak4619.sv", open(os.path.join(vroot, "drivers/ak4619.sv")))
        platform.add_file("cal.sv", open(os.path.join(vroot, "cal/cal.sv")))


    def elaborate(self, platform) -> Module:

        m = Module()

        self.add_verilog_sources(platform)

        pmod_pins = self.pmod_pins

        # 1/256 clk_fs divider. this is not a true clock domain, don't create one.
        # FIXME: this should be removed from `eurorack-pmod` verilog implementation
        # and just replaced with a strobe. that's all its used for anyway. For this
        # reason we do NOT expose this signal and only the 'strobe' version created next.
        clk_fs = Signal()
        clkdiv_fs = Signal(8)
        m.d.audio += clkdiv_fs.eq(clkdiv_fs+1)
        m.d.comb += clk_fs.eq(clkdiv_fs[-1])

        # Create a strobe from the sample clock 'clk_fs` that asserts for 1 cycle
        # per sample in the 'audio' domain. This is useful for latching our samples
        # and hooking up to various signals in our FIFOs external to this module.
        m.submodules.fs_edge = fs_edge = DomainRenamer("audio")(EdgeToPulse())
        m.d.audio += fs_edge.edge_in.eq(clk_fs),
        m.d.comb += self.fs_strobe.eq(fs_edge.pulse_out)

        m.submodules.i2c_master = i2c_master = I2CMaster()

        sample_adc = Signal(data.ArrayLayout(signed(WIDTH), 4))
        sample_dac = Signal(data.ArrayLayout(signed(WIDTH), 4))

        # Hook up I2C master (TODO: use provider)
        m.d.comb += [
            # When i2c oe is asserted, we always want to pull down.
            pmod_pins.i2c_scl.o.eq(0),
            pmod_pins.i2c_sda.o.eq(0),
            pmod_pins.i2c_sda.oe.eq(i2c_master.pins.sda.oe),
            pmod_pins.i2c_scl.oe.eq(i2c_master.pins.scl.oe),
            i2c_master.pins.sda.i.eq(pmod_pins.i2c_sda.i),
            i2c_master.pins.scl.i.eq(pmod_pins.i2c_scl.i),

            # Hook up I2C master registers
            self.jack.eq(i2c_master.jack),
            self.touch[0].eq(i2c_master.touch[0]),
            self.touch[1].eq(i2c_master.touch[1]),
            self.touch[2].eq(i2c_master.touch[2]),
            self.touch[3].eq(i2c_master.touch[3]),
            self.touch[4].eq(i2c_master.touch[4]),
            self.touch[5].eq(i2c_master.touch[5]),
            self.touch[6].eq(i2c_master.touch[6]),
            self.touch[7].eq(i2c_master.touch[7]),
            i2c_master.led[0].eq(self.sample_i[0]>>10),
            i2c_master.led[1].eq(self.sample_i[1]>>10),
            i2c_master.led[2].eq(self.sample_i[2]>>10),
            i2c_master.led[3].eq(self.sample_i[3]>>10),
            i2c_master.led[4].eq(self.sample_o[0]>>10),
            i2c_master.led[5].eq(self.sample_o[1]>>10),
            i2c_master.led[6].eq(self.sample_o[2]>>10),
            i2c_master.led[7].eq(self.sample_o[3]>>10),
        ]

        # CODEC ser-/deserialiser. Sample rate derived from these clocks.
        m.submodules.vak4619 = Instance("ak4619",
            # Parameters
            p_W = WIDTH,

            # Ports (clk + reset)
            i_clk_256fs = ClockSignal("audio"),
            i_strobe = self.fs_strobe,
            i_rst = ResetSignal("audio"),

            # Pads (directly hooked up to pads without extra logic required)
            o_pdn = pmod_pins.pdn.o,
            o_mclk = pmod_pins.mclk.o,
            o_sdin1 = pmod_pins.sdin1.o,
            i_sdout1 = pmod_pins.sdout1.i,
            o_lrck = pmod_pins.lrck.o,
            o_bick = pmod_pins.bick.o,

            o_sample_out0 = sample_adc[0],
            o_sample_out1 = sample_adc[1],
            o_sample_out2 = sample_adc[2],
            o_sample_out3 = sample_adc[3],
            i_sample_in0  = sample_dac[0],
            i_sample_in1  = sample_dac[1],
            i_sample_in2  = sample_dac[2],
            i_sample_in3  = sample_dac[3]
        )

        # Raw sample calibrator, both for input and output channels.
        # Compensates for DC bias in CODEC, gain differences, resistor
        # tolerances and so on.
        m.submodules.vcal = Instance("cal",
            # Parameters
            p_W = WIDTH,

            # Ports (clk + reset)
            i_clk_256fs = ClockSignal("audio"),
            i_strobe = self.fs_strobe,
            i_rst = ResetSignal("audio"),

            # Calibrated inputs are zeroed if jack is unplugged.
            i_jack = self.jack,

            # Note: inputs samples are inverted by analog frontend
            # Should add +1 for precise 2s complement sign change
            i_in0  = ~sample_adc[0],
            i_in1  = ~sample_adc[1],
            i_in2  = ~sample_adc[2],
            i_in3  = ~sample_adc[3],
            i_in4  = self.sample_o[0],
            i_in5  = self.sample_o[1],
            i_in6  = self.sample_o[2],
            i_in7  = self.sample_o[3],
            o_out0 = self.sample_i[0],
            o_out1 = self.sample_i[1],
            o_out2 = self.sample_i[2],
            o_out3 = self.sample_i[3],
            o_out4 = sample_dac[0],
            o_out5 = sample_dac[1],
            o_out6 = sample_dac[2],
            o_out7 = sample_dac[3],
        )

        return m

def pins_from_pmod_connector_with_ribbon(platform, pmod_index):
    """Create a eurorack-pmod resource on a given PMOD connector. Assumes ribbon cable flip."""
    eurorack_pmod = [
        Resource(f"eurorack_pmod{pmod_index}", pmod_index,
            Subsignal("sdin1",   Pins("1",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("sdout1",  Pins("2",  conn=("pmod", pmod_index), dir='i')),
            Subsignal("lrck",    Pins("3",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("bick",    Pins("4",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("mclk",    Pins("10", conn=("pmod", pmod_index), dir='o')),
            Subsignal("pdn",     Pins("9",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("i2c_sda", Pins("8",  conn=("pmod", pmod_index), dir='io')),
            Subsignal("i2c_scl", Pins("7",  conn=("pmod", pmod_index), dir='io')),
            Attrs(IO_TYPE="LVCMOS33"),
        )
    ]
    platform.add_resources(eurorack_pmod)
    return platform.request(f"eurorack_pmod{pmod_index}")

