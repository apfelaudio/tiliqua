git clone https://github.com/apfelaudio/tiliqua --depth=1 && cd tiliqua/gateware && git submodule update --init --recursive && pdm use /workspace/.venv && pdm install && pdm xbeam build --verbose
