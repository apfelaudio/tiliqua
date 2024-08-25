#!/bin/bash
set -e -o pipefail

mkdir -p dmf

# bootloader
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_boot
cp build/top.bit dmf/boot.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_xbeam
cp build/top.bit dmf/bitstream0-xbeam.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_poly
cp build/top.bit dmf/bitstream1-poly.bit
#TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_sid
#cp build/top.bit dmf/bitstream2-sid.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_usb_audio
cp build/top.bit dmf/bitstream3-usb-audio.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_dsp_core nco
cp build/top.bit dmf/bitstream4-quad-nco.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_dsp_core diffuser
cp build/top.bit dmf/bitstream5-diffuser.bit
TILIQUA_RESOLUTION=720x720p60 TILIQUA_VIDEO_ROTATE=1 TILIQUA_HPD_HACK=1 pdm build_selftest
cp build/top.bit dmf/bitstream6-selftest.bit
TILIQUA_RESOLUTION=1280x720p60 pdm build_xbeam
cp build/top.bit dmf/bitstream7-xbeam-hd.bit
