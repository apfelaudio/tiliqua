# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Some utilities useful for unit testing Tiliqua components.
"""

def wb_transaction_params(register_start_bytes, field_start_bits,
                          field_width_bits, word_sz=4):
    """Convert register byte/bit indices into wishbone transaction arguments."""
    # include bit offset in byte offset if it's > 8
    register_start_bytes += field_start_bits // 8
    field_start_bits %= 8
    # compute minimum bytes needed to contain field (used for sel)
    field_width_bytes_ceil  = field_width_bits // 8
    if field_width_bits % 8 != 0:
        field_width_bytes_ceil += 1
    # field offset from the word, in bytes
    ix_bytes = register_start_bytes % word_sz
    # compute bus access
    # FIXME: technically this may give us things like sel=0b0110,
    # which probably no CPU would ever do...
    wb_adr = register_start_bytes // word_sz
    wb_sel = int('1'*field_width_bytes_ceil, base=2) << ix_bytes
    # shift needed to pluck out field from wishbone dat_w, dat_r
    dat_shift = ix_bytes*8 + field_start_bits
    return wb_adr, wb_sel, dat_shift, field_width_bits

def wb_register(mmap_bus, register_name, field_name=None):
    """
    Find a register (optionally subfield) in a bus memory map.
    Return arguments required for a wishbone transaction to access it.
    """
    for (reg_object, name, (s_byte, e_byte)) in mmap_bus.memory_map.resources():
        name = str(name)[6:-2]
        if name == register_name:
            if field_name is None:
                return wb_transaction_params(
                    register_start_bytes=s_byte,
                    field_start_bits=0,
                    field_width_bits=(e_byte - s_byte)*8
                )
            offset = 0
            for path, action in reg_object:
                name = "_".join([str(s) for s in path])
                width = action.port.shape.width
                if name == field_name:
                    return wb_transaction_params(
                        register_start_bytes=s_byte,
                        field_start_bits=offset,
                        field_width_bits=width
                    )
                offset += width
    raise ValueError(f"{register_name} {field_name} does not exist in memory map.")

async def wb_transaction(ctx, wb_bus, adr, we, sel, dat_w=None):
    ctx.set(wb_bus.cyc, 1)
    ctx.set(wb_bus.sel, sel)
    ctx.set(wb_bus.we,  we)
    ctx.set(wb_bus.adr, adr)
    ctx.set(wb_bus.stb, 1)
    if we:
        ctx.set(wb_bus.dat_w, dat_w)
    await ctx.tick().repeat(5)
    assert ctx.get(wb_bus.ack) == 1
    value = ctx.get(wb_bus.dat_r) if not we else None
    ctx.set(wb_bus.stb, 0)
    await ctx.tick()
    assert ctx.get(wb_bus.ack) == 0
    return value

async def wb_csr_w(ctx, mmap_bus, wb_bus, value, register_name, field_name=None):
    adr, sel, shift, _ = wb_register(mmap_bus, register_name, field_name)
    return await wb_transaction(ctx, wb_bus, adr, 1, sel, dat_w=value<<shift)

async def wb_csr_r(ctx, mmap_bus, wb_bus, register_name, field_name=None):
    adr, sel, shift, w_bits = wb_register(mmap_bus, register_name, field_name)
    value_32b = await wb_transaction(ctx, wb_bus, adr, 0, sel)
    return (value_32b >> shift) & int('1'*w_bits, base=2)
