# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Low-level drivers and domain crossing logic for `eurorack-pmod` hardware."""

import os

from amaranth                   import *
from amaranth.build             import *
from amaranth.lib               import wiring, data, stream
from amaranth.lib.wiring        import In, Out
from amaranth.lib.fifo          import AsyncFIFOBuffered
from amaranth.lib.cdc           import FFSynchronizer
from amaranth.lib.memory        import Memory

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
                width=self.eurorack_pmod.i_cal.payload.shape().size, depth=self.fifo_depth,
                w_domain="audio", r_domain=self.stream_domain)
        m.submodules.dac_fifo = dac_fifo = AsyncFIFOBuffered(
                width=self.eurorack_pmod.o_cal.payload.shape().size, depth=self.fifo_depth,
                w_domain=self.stream_domain, r_domain="audio")

        wiring.connect(m, adc_fifo.r_stream, wiring.flipped(self.istream))
        wiring.connect(m, wiring.flipped(self.ostream), dac_fifo.w_stream)

        eurorack_pmod = self.eurorack_pmod

        wiring.connect(m, self.eurorack_pmod.o_cal, adc_fifo.w_stream)
        wiring.connect(m, dac_fifo.r_stream, self.eurorack_pmod.i_cal)

        return m

class AK4619(wiring.Component):

    """
    This core talks I2S TDM to an AK4619 configured in the
    interface mode configured by I2CMaster below.

    The following registers specify the interface format:
     - FS == 0b000, which means:
         - MCLK = 256*Fs,
         - BICK = 128*Fs,
         - Fs must fall within 8kHz <= Fs <= 48Khz.
    - TDM == 0b1 and DCF == 0b010, which means:
         - TDM128 mode I2S compatible.
    """

    N_CHANNELS = 4
    S_WIDTH    = 16
    SLOT_WIDTH = 32

    mclk:   Out(1)
    bick:   Out(1)
    lrck:   Out(1)
    sdin1:  Out(1)
    sdout1: In(1)

    i: In(stream.Signature(signed(16)))
    o: Out(stream.Signature(signed(16)))

    def elaborate(self, platform):
        m = Module()
        clkdiv      = Signal(8)
        bit_counter = Signal(5)
        bitsel      = Signal(range(self.S_WIDTH))
        sample_i    = Signal(signed(self.S_WIDTH))
        m.d.comb += [
            self.mclk  .eq(ClockSignal("audio")), # TODO 192KHz
            self.bick  .eq(clkdiv[0]),
            self.lrck  .eq(clkdiv[7]),
            bit_counter.eq(clkdiv[1:6]),
            bitsel.eq(self.S_WIDTH-bit_counter-1),
        ]
        m.d.audio += clkdiv.eq(clkdiv+1)
        with m.If(bit_counter == (self.SLOT_WIDTH-1)):
            with m.If(self.bick):
                m.d.audio += self.o.payload.eq(0)
            with m.Else():
                m.d.comb += [
                    self.i.ready.eq(1),
                    self.o.valid.eq(1),
                ]
        with m.If(self.bick):
            # BICK transition HI -> LO: Clock in W bits
            # On HI -> LO both SDIN and SDOUT do not transition.
            # (determined by AK4619 transition polarity register BCKP)
            with m.If(bit_counter < self.S_WIDTH):
                m.d.audio += self.o.payload.eq((self.o.payload << 1) | self.sdout1)
        with m.Else():
            # BICK transition LO -> HI: Clock out W bits
            # On LO -> HI both SDIN and SDOUT transition.
            with m.If(bit_counter < (self.S_WIDTH-1)):
                m.d.audio += self.sdin1.eq(self.i.payload.bit_select(bitsel, 1))
            with m.Else():
                m.d.audio += self.sdin1.eq(0)
        return m

class Calibrator(wiring.Component):

    i_uncal:  In(stream.Signature(signed(AK4619.S_WIDTH)))
    o_cal:   Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    i_cal:    In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o_uncal: Out(stream.Signature(signed(AK4619.S_WIDTH)))

    def elaborate(self, platform):

        m = Module()

        channel = Signal(2)

        m.submodules.mem_cal_in = mem_cal_in = Memory(
            shape=data.ArrayLayout(signed(16), 2), depth=4, init=[
            [0xff63, 0x485] for _ in range(4)
        ])

        mem_cal_in_rport = mem_cal_in.read_port(domain="audio")

        m.submodules.mem_cal_out = mem_cal_out = Memory(
            shape=data.ArrayLayout(signed(16), 2), depth=4, init=[
            [0xfd15, 0x3e8] for _ in range(4)
        ])

        mem_cal_out_rport = mem_cal_out.read_port(domain="audio")

        m.d.comb += [
            mem_cal_in_rport.en.eq(1),
            mem_cal_out_rport.en.eq(1),
            mem_cal_in_rport.addr.eq(channel),
            mem_cal_out_rport.addr.eq(channel),
        ]

        latch = Signal.like(self.i_cal.payload)
        m.d.sync += self.o_cal.valid.eq(0)
        i_select = Signal(signed(16))
        m.d.comb += i_select.eq(
            self.i_cal.payload.as_value().bit_select(
                channel*AK4619.S_WIDTH, AK4619.S_WIDTH))
        with m.If(self.i_uncal.valid):
            m.d.comb += [
                self.i_cal.ready.eq(1),
                self.o_uncal.valid.eq(1),
                self.o_uncal.payload.eq(
                    ((i_select - mem_cal_out_rport.data[0]) * mem_cal_in_rport.data[1])>>10)
            ]
            m.d.audio += [
                self.o_cal.payload.as_value().bit_select(
                    channel*AK4619.S_WIDTH, AK4619.S_WIDTH).eq(
                        ((self.i_uncal.payload - mem_cal_in_rport.data[0]) *
                          mem_cal_in_rport.data[1])>>10),
                channel.eq(channel+1),
            ]
            with m.If(channel == 0):
                m.d.sync += self.o_cal.valid.eq(1)
                m.d.audio += latch.eq(self.i_cal.payload),

        return m

class I2CMaster(wiring.Component):

    """
    Driver for I2C traffic to/from the `eurorack-pmod`.

    For HW Rev. 3.2+, this is:
       - AK4619 Audio Codec (I2C for configuration only, data is I2S)
       - 24AA025UIDT I2C EEPROM with unique ID
       - PCA9635 I2C PWM LED controller
       - PCA9557 I2C GPIO expander (for jack detection)
       - CY8CMBR3108 I2C touch/proximity sensor (experiment, off by default!)

    This kind of stateful stuff is often best suited for a softcore rather
    than pure RTL, however I wanted to make it possible to use all
    functions of the board without having to resort to using a softcore.
    """

    PCA9557_ADDR     = 0x18
    PCA9635_ADDR     = 0x5
    AK4619VN_ADDR    = 0x10
    CY8CMBR3108_ADDR = 0x37

    N_JACKS   = 8
    N_LEDS    = N_JACKS * 2
    N_SENSORS = 8

    AK4619VN_CFG_48KHZ = [
        0x00, # Register address to start at.
        0x36, # 0x00 Power Management (RSTN asserted!)
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
        0x3A, # 0x14 DAC Mute & Filter Setting (soft mute asserted!)
    ]

    AK4619VN_CFG_192KHZ = AK4619VN_CFG_48KHZ.copy()
    AK4619VN_CFG_192KHZ[4] = 0x04 # 0x03 System Clock Setting

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

    def __init__(self, audio_192):
        self.i2c_stream   = i2c.I2CStreamer(period_cyc=256) # 200kHz-ish at 60MHz sync
        self.audio_192    = audio_192
        self.ak4619vn_cfg = self.AK4619VN_CFG_192KHZ if audio_192 else self.AK4619VN_CFG_48KHZ
        super().__init__({
            "pins":           Out(vendor_i2c.I2CPinSignature()),
            # Jack insertion status.
            "jack":           Out(self.N_JACKS),
            # Desired LED state -green/+red
            "led":            In(signed(8)).array(self.N_JACKS),
            # Touch sensor states
            "touch":          Out(unsigned(8)).array(self.N_SENSORS),
            # should be close to 0 if touch sense is OK.
            "touch_err":      Out(unsigned(8)),
            # assert for at least 100msec for complete muting sequence.
            "codec_mute":     In(1),
        })

    def elaborate(self, platform):
        m = Module()

        m.submodules.i2c_stream = i2c = self.i2c_stream
        wiring.connect(m, wiring.flipped(self.pins), self.i2c_stream.pins)

        def state_id(ix):
            return (f"i2c_state{ix}", f"i2c_state{ix+1}", ix+1)

        def i2c_addr(m, ix, addr):
            # set i2c address of transactions being enqueued
            cur, nxt, ix = state_id(ix)
            with m.State(cur):
                m.d.sync += i2c.address.eq(addr),
                m.next = nxt
            return cur, nxt, ix

        def i2c_write(m, ix, data, last=False):
            # enqueue a single byte. delineate transaction boundary with 'last=True'
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
            # enqueue write transactions for an array of data
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
            # enqueue a single read transaction
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
            # wait until all enqueued transactions are complete
            cur,  nxt, ix = state_id(ix)
            with m.State(cur):
                with m.If(~i2c.status.busy):
                    m.next = nxt
            return cur, nxt, ix


        # used for implicit state machine ID tracking / generation
        ix = 0

        # compute actual LED register values based on signed 'red/green' desire
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

        # current touch sensor to poll, incremented once per loop
        touch_nsensor = Signal(range(self.N_SENSORS))

        #
        # Compute codec power management register contents,
        # Muting effectively clears/sets the RSTN bit and DA1/DA2
        # soft mute bits. `mute_count` ensures correct sequencing -
        # always soft mute before asserting RSTN. Likewise, always
        # boot with soft mute, and deassert soft mute after RSTN.
        #
        # Clocks - assert RSTN (0) to mute, after MCLK is stable.
        # deassert RSTN (1) to unmute, after MCLK is stable.
        #
        mute_count  = Signal(8)

        # CODEC DAC soft mute sequencing
        codec_reg14 = Signal(8)
        with m.If(self.codec_mute):
            # DA1MUTE / DA2MUTE soft mute ON
            m.d.comb += codec_reg14.eq(self.ak4619vn_cfg[0x15] | 0b00110000)
        with m.Else():
            # DA1MUTE / DA2MUTE soft mute OFF
            m.d.comb += codec_reg14.eq(self.ak4619vn_cfg[0x15] & 0b11001111)

        # CODEC RSTN sequencing
        # Only assert if we know soft mute has been asserted for a while.
        codec_reg00 = Signal(8)
        with m.If(mute_count == 0xff):
            m.d.comb += codec_reg00.eq(self.ak4619vn_cfg[1] & 0b11111110)
        with m.Else():
            m.d.comb += codec_reg00.eq(self.ak4619vn_cfg[1] | 0b00000001)

        startup_delay = Signal(32)

        with m.FSM(init='STARTUP-DELAY') as fsm:

            #
            # AK4619VN init
            #
            init, _,   ix  = i2c_addr (m, ix, self.AK4619VN_ADDR)
            _,    _,   ix  = i2c_w_arr(m, ix, self.ak4619vn_cfg)
            _,    _,   ix  = i2c_wait (m, ix)

            #
            # startup delay
            #

            with m.State('STARTUP-DELAY'):
                if platform is not None:
                    with m.If(startup_delay == 600_000):
                        m.next = init
                    with m.Else():
                        m.d.sync += startup_delay.eq(startup_delay+1)
                else:
                    m.next = init

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
                    with m.If(self.touch_err > 0):
                        m.d.sync += self.touch_err.eq(self.touch_err - 1)
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
                with m.Else():
                    with m.If(self.touch_err != 0xff):
                        m.d.sync += self.touch_err.eq(self.touch_err + 1)
                m.next = nxt


            # AK4619VN power management (Soft mute + RSTN)

            _,   _,   ix  = i2c_addr (m, ix, self.AK4619VN_ADDR)
            _,   _,   ix  = i2c_write(m, ix, 0x00) # RSTN
            _,   _,   ix  = i2c_write(m, ix, codec_reg00, last=True)
            _,   _,   ix  = i2c_wait (m, ix)

            _,   _,   ix  = i2c_write(m, ix, 0x14) # DAC1MUTE / DAC2MUTE
            _,   _,   ix  = i2c_write(m, ix, codec_reg14, last=True)
            _,   _,   ix  = i2c_wait (m, ix)

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
                # Also update the soft mute state tracking
                with m.If(self.codec_mute):
                    with m.If(mute_count != 0xff):
                        m.d.sync += mute_count.eq(mute_count+1)
                with m.Else():
                    m.d.sync += mute_count.eq(0)
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

    i_cal:  In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o_cal: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    # Touch sensing and jacksense outputs.
    touch: Out(8).array(8)
    jack: Out(8)
    touch_err: Out(8)
    codec_mute: In(1)

    # 1s for automatic audio -> LED control. 0s for manual.
    led_mode: In(8, init=0xff)
    # If an LED is in manual, this is signed i8 from -green to +red.
    led: In(8).array(8)

    # TODO
    # Read from the onboard I2C eeprom.
    # These will be valid a few hundred milliseconds after boot.
    eeprom_mfg: Out(8)
    eeprom_dev: Out(8)
    eeprom_serial: Out(32)

    # Signals only used for calibration
    sample_adc: Out(signed(WIDTH)).array(4)
    # TODO
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

        define_192 = "`define AK4619_192KHZ" if self.audio_192 else ""
        platform.add_file("eurorack_pmod_defines.sv",
                          f"`define HW_R33\n{define_192}")
        platform.add_file("cal/cal_mem_default_r33.hex",
                          open(os.path.join(vroot, "cal/cal_mem_default_r33.hex")))

        # Verilog implementation
        platform.add_file("ak4619.sv", open(os.path.join(vroot, "drivers/ak4619.sv")))
        platform.add_file("cal.sv", open(os.path.join(vroot, "cal/cal.sv")))


    def elaborate(self, platform) -> Module:

        m = Module()

        self.add_verilog_sources(platform)

        m.submodules.i2c_master = i2c_master = I2CMaster(audio_192=self.audio_192)

        pmod_pins = self.pmod_pins

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
            self.touch_err.eq(i2c_master.touch_err),

            # Hook up coded mute control
            i2c_master.codec_mute.eq(self.codec_mute),
        ]

        for n in range(8):

            # Touch sense readings per jack
            m.d.comb += self.touch[n].eq(i2c_master.touch[n]),

            # LED auto/manual settings per jack
            with m.If(self.led_mode[n]):
                if n <= 3:
                    m.d.comb += i2c_master.led[n].eq(self.i_cal.payload[n].raw()>>8),
                else:
                    m.d.comb += i2c_master.led[n].eq(self.o_cal.payload[n-4].raw()>>8),
            with m.Else():
                m.d.comb += i2c_master.led[n].eq(self.led[n]),

        # PDN (and clocking for mobo R3+ for pop-free bitstream switching)
        m.d.comb += pmod_pins.pdn_d.o.eq(1),
        if hasattr(pmod_pins, "pdn_clk"):
            #
            # Drive external flip-flop, ensuring PDN remains high across
            # FPGA reconfiguration (only works on mobo R3+).
            #
            # Codec RSTN must be asserted (held in reset) across the
            # FPGA reconfiguration. This is performed by `self.codec_mute`.
            #
            pdn_cnt = Signal(unsigned(16))
            with m.If(pdn_cnt != 60000): # 1ms
                m.d.sync += pdn_cnt.eq(pdn_cnt+1)
            with m.If(3000 < pdn_cnt):
                m.d.comb += pmod_pins.pdn_clk.o.eq(1)

        m.submodules.ak4619 = ak4619 = AK4619()
        m.submodules.calibrator = calibrator = Calibrator()
        wiring.connect(m, ak4619.o, calibrator.i_uncal)
        wiring.connect(m, calibrator.o_uncal, ak4619.i)
        wiring.connect(m, calibrator.o_cal, wiring.flipped(self.o_cal))
        wiring.connect(m, wiring.flipped(self.i_cal), calibrator.i_cal)

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

