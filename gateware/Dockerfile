FROM ubuntu:22.04

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3.10-venv \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install PDM
RUN curl -sSL https://pdm-project.org/install-pdm.py | python3 -
ENV PATH="/root/.local/bin:${PATH}"

# Install Python tools globally
RUN pip install pytest

# Install Rust with default toolchain
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable && \
    . $HOME/.cargo/env && \
    rustup component add rustfmt clippy llvm-tools && \
    rustup target add riscv32im-unknown-none-elf riscv32imafc-unknown-none-elf && \
    cargo install cargo-binutils form svd2rust --locked

# Add cargo environment to PATH and set environment variables
ENV PATH="/root/.cargo/bin:${PATH}"
ENV CARGO_HOME="/root/.cargo"
ENV RUSTUP_HOME="/root/.rustup"

# Set up working directory
WORKDIR /workspace

# Pre-install PDM dependencies
COPY pyproject.toml pdm.lock ./
RUN pdm install

# Clean up
RUN rm -rf pyproject.toml pdm.lock 
