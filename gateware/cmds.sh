# setup
cargo install svd2rust form

# generate pac
pdm run src/example_soc/top.py --generate-svd > build/example_soc.svd
cd src/example_soc/pac && make

# generate memory.x
pdm run src/example_soc/top.py --generate-memory-x > build/memory.x
cp build/memory.x src/example_soc/fw

# build FW
cargo build --release
cargo objcopy --release -- -Obinary firmware.bin

# make sure path is correct in top.py of firmware.bin
# build bitstream
pdm run src/example_soc/top.py --dry-run
