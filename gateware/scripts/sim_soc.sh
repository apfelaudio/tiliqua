#!/bin/bash

set -e -o pipefail

SOC=src/$1

# generate artifacts from design
mkdir -p build
pdm run $SOC/top.py --genrust

# create the PAC
make -C src/rs/pac

# move linker script into fw project (TODO: cleaner to leave in build/?)
cp build/memory.x $SOC/fw/memory.x

# build firmware itself and turn it into a binary stream
(cd $SOC/fw && cargo build --release)
(cd $SOC/fw && cargo objcopy --release -- -Obinary firmware.bin)

# simulate final bitstream
pdm run $SOC/top.py --sim
