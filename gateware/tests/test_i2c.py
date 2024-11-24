# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring, data
from amaranth.lib.memory   import Memory
from tiliqua               import i2c, test_util

from amaranth_soc          import csr
from amaranth_soc.csr      import wishbone

from vendor                import i2c as vendor_i2c

class PmodMaster(wiring.Component):

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
        self.i2c_stream = i2c.I2CStreamer(period_cyc=4)
        super().__init__({
            "pins": wiring.Out(vendor_i2c.I2CPinSignature()),
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
                with m.If(cnt != len(data) + 1):
                    m.d.sync += cnt.eq(cnt+1)
                    m.d.comb += [
                        i2c.i.valid.eq(1),
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

        with m.FSM() as fsm:

            #
            # PCA9557 init
            #

            cur, _,   ix  = i2c_addr (m, ix, self.PCA9557_ADDR)
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
            # AK4619VN init
            #
            _,   _,   ix  = i2c_addr (m, ix, self.AK4619VN_ADDR)
            _,   _,   ix  = i2c_w_arr(m, ix, self.AK4619VN_CFG)
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

class I2CTests(unittest.TestCase):

    def test_i2c_peripheral(self):

        m = Module()
        dut = i2c.Peripheral(period_cyc=4)
        decoder = csr.Decoder(addr_width=28, data_width=8)
        decoder.add(dut.bus, addr=0, name="dut")
        bridge = wishbone.WishboneCSRBridge(decoder.bus, data_width=32)
        m.submodules += [dut, decoder, bridge]

        async def test_stimulus(ctx):

            async def csr_write(ctx, value, register, field=None):
                await test_util.wb_csr_w(
                        ctx, dut.bus, bridge.wb_bus, value, register, field)

            async def csr_read(ctx, register, field=None):
                return await test_util.wb_csr_r(
                        ctx, dut.bus, bridge.wb_bus, register, field)

            # set device address
            await csr_write(ctx, 0x55, "address")

            # enqueue 2x write ops
            await csr_write(ctx, 0x042, "transaction_reg")
            await csr_write(ctx, 0x013, "transaction_reg")

            # enqueue 1x read + last op
            await csr_write(ctx, 0x300, "transaction_reg")

            # 3 transactions are enqueued
            self.assertEqual(ctx.get(dut.i2c_stream._transactions.level), 3)

            # busy flag should go high
            self.assertEqual(await csr_read(ctx, "status", "busy"), 1)

            await ctx.tick().repeat(500)

            # busy flag should be low
            self.assertEqual(await csr_read(ctx, "status", "busy"), 0)

            # all transactions drained.
            self.assertEqual(ctx.get(dut.i2c_stream._transactions.level), 0)

        async def test_response(ctx):

            was_busy = False
            data_written = []
            while True:
                await ctx.tick()
                if ctx.get(dut.i2c_stream.status.busy) and not was_busy:
                    was_busy = True
                if was_busy and not ctx.get(dut.i2c_stream.status.busy):
                    break
                if ctx.get(dut.i2c_stream.i2c.start):
                    print("i2c.start")
                if ctx.get(dut.i2c_stream.i2c.write):
                    v = ctx.get(dut.i2c_stream.i2c.data_i)
                    print("i2c.write", hex(v))
                    data_written.append(v)
                if ctx.get(dut.i2c_stream.i2c.read):
                    print("i2c.read",  hex(ctx.get(dut.i2c_stream.i2c.data_o)))
                if ctx.get(dut.i2c_stream.i2c.stop):
                    print("i2c.stop")

            self.assertEqual(data_written, [0xaa, 0x42, 0x13, 0xab])

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(test_stimulus)
        sim.add_testbench(test_response, background=True)
        with sim.write_vcd(vcd_file=open("test_i2c_peripheral.vcd", "w")):
            sim.run()

    def test_i2c_master(self):

        m = Module()
        dut = PmodMaster()
        m.submodules += [dut]

        async def test_response(ctx):
            was_busy = False
            data_written = []
            ctx.set(dut.led[0], -10)
            ctx.set(dut.led[1], 10)
            while True:
                await ctx.tick()
                """
                if ctx.get(dut.i2c_stream.status.busy) and not was_busy:
                    was_busy = True
                if was_busy and not ctx.get(dut.i2c_stream.status.busy):
                    break
                """
                if ctx.get(dut.i2c_stream.i2c.start):
                    print("i2c.start")
                if ctx.get(dut.i2c_stream.i2c.write):
                    v = ctx.get(dut.i2c_stream.i2c.data_i)
                    print("i2c.write", hex(v))
                    data_written.append(v)
                if ctx.get(dut.i2c_stream.i2c.read):
                    print("i2c.read",  hex(ctx.get(dut.i2c_stream.i2c.data_o)))
                if ctx.get(dut.i2c_stream.i2c.stop):
                    print("i2c.stop")

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(test_response)
        with sim.write_vcd(vcd_file=open("test_i2c_peripheral.vcd", "w")):
            sim.run()

    def test_i2c_luna_register_interface(self):

        m = Module()
        dut = vendor_i2c.I2CRegisterInterface(period_cyc=4, max_data_bytes=16)
        m.submodules += [dut]

        async def testbench(ctx):
            ctx.set(dut.dev_address,   0x5)
            ctx.set(dut.reg_address,   0x42)
            ctx.set(dut.size,          4)
            ctx.set(dut.write_request, 1)
            ctx.set(dut.write_data[-32:], 0xDEADBEEF)
            await ctx.tick()
            ctx.set(dut.write_request, 0)
            data_written = []
            print()
            while ctx.get(dut.busy):
                if ctx.get(dut.i2c.start):
                    print("i2c.start")
                if ctx.get(dut.i2c.write):
                    v = ctx.get(dut.i2c.data_i)
                    print("i2c.write", hex(v))
                    data_written.append(v)
                if ctx.get(dut.i2c.read):
                    print("i2c.read",  hex(ctx.get(dut.i2c.data_o)))
                if ctx.get(dut.i2c.stop):
                    print("i2c.stop")
                await ctx.tick()

            self.assertEqual(data_written, [0xa, 0x42, 0xde, 0xad, 0xbe, 0xef])

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_i2c_luna_register_interface.vcd", "w")):
            sim.run()
