#!/bin/bash
set -e -o pipefail

mkdir -p dmf

# bootloader
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 TILIQUA_BITSTREAM_NAME="TILIQUA BOOT" pdm build_boot
cp build/top.bit dmf/boot.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 TILIQUA_BITSTREAM_NAME="XBEAM" pdm build_xbeam
cp build/top.bit dmf/bitstream0-xbeam.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 TILIQUA_BITSTREAM_NAME="POLYSYN" pdm build_poly
cp build/top.bit dmf/bitstream1-poly.bit
#TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_sid
#cp build/top.bit dmf/bitstream2-sid.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 TILIQUA_BITSTREAM_NAME="USB-AUDIO" pdm build_usb_audio
cp build/top.bit dmf/bitstream3-usb-audio.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 TILIQUA_XBEAM_CORE=nco TILIQUA_BITSTREAM_NAME="QUAD-NCO" pdm build_xbeam
cp build/top.bit dmf/bitstream4-quad-nco.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 TILIQUA_XBEAM_CORE=diffuser TILIQUA_BITSTREAM_NAME="DIFFUSER" pdm build_xbeam
cp build/top.bit dmf/bitstream5-diffuser.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 TILIQUA_BITSTREAM_NAME="SELFTEST" pdm build_selftest
cp build/top.bit dmf/bitstream6-selftest.bit
TILIQUA_RESOLUTION=1280x720p60 pdm build_xbeam
cp build/top.bit dmf/bitstream7-xbeam-hd.bit
