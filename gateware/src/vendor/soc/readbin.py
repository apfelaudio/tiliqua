import logging, math, os, struct

def get_mem_regions(filename_or_regions, offset):
    if isinstance(filename_or_regions, dict):
        regions = filename_or_regions
    else:
        filename = filename_or_regions
        if not os.path.isfile(filename):
            raise FileNotFoundError(f"Unable to find '{filename}' memory content file.")
        _, ext = os.path.splitext(filename)
        if ext == ".json":
            f = open(filename, "r")
            _regions = json.load(f)
            # .json
            regions = dict()
            for k, v in _regions.items():
                regions[os.path.join(os.path.dirname(filename), k)] = v
            f.close()
        else:
            regions = {filename: f"{offset:08x}"}
    return regions

def get_mem_data(filename_or_regions, data_width=32, endianness="big", mem_size=None, offset=0):
    assert data_width % 32 == 0
    assert endianness in ["big", "little"]

    # Create memory regions.
    regions = get_mem_regions(filename_or_regions, offset)
    if regions is None:
        return None

    # Determine data_size.
    data_size = 0
    for filename, base in regions.items():
        if not os.path.isfile(filename):
            raise OSError(f"Unable to find '{filename}' memory content file.")
        data_size = max(int(base, 16) + os.path.getsize(filename) - offset, data_size)
    assert data_size > 0
    if mem_size is not None:
        assert data_size < mem_size, (
            "file is too big: {}/{} bytes".format(
             data_size, mem_size))

    # Fill data.
    bytes_per_data = data_width//8
    data           = [0]*math.ceil(data_size/bytes_per_data)
    for filename, base in regions.items():
        base = int(base, 16)
        with open(filename, "rb") as f:
            i = 0
            while True:
                w = f.read(bytes_per_data)
                if not w:
                    break
                if len(w) != bytes_per_data:
                    for _ in range(len(w), bytes_per_data):
                        w += b'\x00'
                unpack_order = {
                    "little": "<I",
                    "big":    ">I"
                }[endianness]
                data[(base - offset)//bytes_per_data + i] = 0
                for filled_data_width in range(0, data_width, 32):
                    cur_byte = filled_data_width//8
                    data[(base - offset)//bytes_per_data + i] |= (struct.unpack(unpack_order, w[cur_byte:cur_byte+4])[0] << filled_data_width)
                i += 1
    return data

