#!/bin/bash
set -e -o pipefail

sudo openFPGALoader -c dirtyJtag dmf/boot.bit                 -f -o 0x0
sudo openFPGALoader -c dirtyJtag dmf/bitstream0-xbeam.bit     -f -o 0x100000
sudo openFPGALoader -c dirtyJtag dmf/bitstream1-poly.bit      -f -o 0x200000
#sudo openFPGALoader -c dirtyJtag dmf/bitstream2-sid.bit      -f -o 0x300000
sudo openFPGALoader -c dirtyJtag dmf/bitstream3-usb-audio.bit -f -o 0x400000
sudo openFPGALoader -c dirtyJtag dmf/bitstream4-quad-nco.bit  -f -o 0x500000
sudo openFPGALoader -c dirtyJtag dmf/bitstream5-diffuser.bit  -f -o 0x600000
sudo openFPGALoader -c dirtyJtag dmf/bitstream6-selftest.bit  -f -o 0x700000
sudo openFPGALoader -c dirtyJtag dmf/bitstream7-xbeam-hd.bit  -f -o 0x800000
