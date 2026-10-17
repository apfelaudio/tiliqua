//! Tiny EDID parser, only handles the header and detailed timing descriptor.
//! Does not handle extension blocks. This should be enough for most small embedded monitors.

/// Main EDID structure representing the first 128 bytes of an EDID block
#[derive(Debug)]
pub struct Edid {
    // Header (bytes 0-19)
    pub header: EdidHeader,
    // Detailed timing descriptors (bytes 54-125)
    pub descriptors: [Descriptor; 4],
    // Extension flag (byte 126)
    pub extensions: u8,
    // Checksum (byte 127)
    pub checksum: u8,
}

/// EDID Header information (bytes 0-19)
#[derive(Debug)]
pub struct EdidHeader {
    // Fixed header pattern (bytes 0-7)
    pub pattern: [u8; 8],
    // Manufacturer ID (bytes 8-9)
    pub manufacturer_id: [u8; 2],
    // Manufacturer product code (bytes 10-11)
    pub product_code: u16,
    // Serial number (bytes 12-15)
    pub serial_number: u32,
    // Week of manufacture (byte 16)
    pub manufacture_week: u8,
    // Year of manufacture (byte 17)
    pub manufacture_year: u8,
    // EDID version & revision (bytes 18-19)
    pub version: u8,
    pub revision: u8,
}

/// Detailed timing descriptors (18 bytes each)
/// For simplicity, we're just storing the raw data for now
#[derive(Debug, Copy, Clone)]
pub struct RawDescriptor {
    pub data: [u8; 18],
}

/// Descriptor types for the 18-byte descriptor blocks
#[derive(Debug, Copy, Clone)]
pub enum Descriptor {
    DetailedTiming(DetailedTimingDescriptor),
    RawDescriptor([u8; 18]),
}

/// Detailed timing descriptor (used when pixel clock != 0)
#[derive(Debug, Copy, Clone)]
pub struct DetailedTimingDescriptor {
    pub pixel_clock_khz: u32,  // in 10 kHz units
    pub horizontal_active: u16,
    pub horizontal_blanking: u16,
    pub vertical_active: u16,
    pub vertical_blanking: u16,
    pub horizontal_sync_offset: u16,
    pub horizontal_sync_pulse_width: u16,
    pub vertical_sync_offset: u16,
    pub vertical_sync_pulse_width: u16,
    pub horizontal_image_size_mm: u16,
    pub vertical_image_size_mm: u16,
    pub horizontal_border: u8,
    pub vertical_border: u8,
    pub features: TimingFeatures,
}

/// Timing features bitmap
#[derive(Debug, Copy, Clone)]
pub struct TimingFeatures {
    pub interlaced: bool,
    pub stereo_mode: StereoMode,
    pub sync_type: SyncType,
}

/// Stereo mode options
#[derive(Debug, Copy, Clone)]
pub enum StereoMode {
    None,
    FieldSequentialRight,
    FieldSequentialLeft,
    InterleavedRightEven,
    InterleavedLeftEven,
    FourWayInterleaved,
    SideBySideInterleaved,
}

/// Sync type options
#[derive(Debug, Copy, Clone)]
pub enum SyncType {
    Analog {
        bipolar: bool,
        serration: bool,
        sync_on_rgb: bool,
    },
    DigitalComposite {
        serration: bool,
        hsync_positive: bool,
    },
    DigitalSeparate {
        vsync_positive: bool,
        hsync_positive: bool,
    },
}

impl Edid {
    /// Parse the EDID from raw bytes
    pub fn parse(edid_data: &[u8; 128]) -> Result<Self, EdidError> {
        // Verify checksum
        let mut checksum: u8 = 0;
        for &byte in edid_data {
            checksum = checksum.wrapping_add(byte);
        }
        if checksum != 0 {
            return Err(EdidError::InvalidChecksum);
        }
        // Parse EDID header
        let header = EdidHeader {
            pattern: [
                edid_data[0], edid_data[1], edid_data[2], edid_data[3],
                edid_data[4], edid_data[5], edid_data[6], edid_data[7],
            ],
            manufacturer_id: [edid_data[8], edid_data[9]],
            product_code: u16::from_le_bytes([edid_data[10], edid_data[11]]),
            serial_number: u32::from_le_bytes([
                edid_data[12], edid_data[13], edid_data[14], edid_data[15],
            ]),
            manufacture_week: edid_data[16],
            manufacture_year: edid_data[17],
            version: edid_data[18],
            revision: edid_data[19],
        };
        // Verify header pattern
        if header.pattern != [0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00] {
            return Err(EdidError::InvalidHeaderPattern);
        }
        let mut descriptors = [Descriptor::RawDescriptor([0; 18]); 4];
        for (i, descriptor) in descriptors.iter_mut().enumerate() {
            let offset = 54 + i * 18;
            let mut data = [0; 18];
            data.copy_from_slice(&edid_data[offset..offset + 18]);
            // Parse the descriptor based on its format
            *descriptor = Self::parse_descriptor(&data);
        }

        Ok(Self {
            header,
            descriptors,
            extensions: edid_data[126],
            checksum: edid_data[127],
        })
    }

    /// Parse a descriptor block
    fn parse_descriptor(data: &[u8; 18]) -> Descriptor {
        // Check if it's a detailed timing descriptor (pixel clock != 0)
        let pixel_clock = u16::from_le_bytes([data[0], data[1]]);
        if pixel_clock != 0 {
            // Detailed timing descriptor
            return Descriptor::DetailedTiming(Self::parse_detailed_timing(data));
        }
        // It's not a detailed timing descriptor, store raw data
        Descriptor::RawDescriptor(*data)
    }

    /// Parse a detailed timing descriptor
    fn parse_detailed_timing(data: &[u8; 18]) -> DetailedTimingDescriptor {
        let pixel_clock = u16::from_le_bytes([data[0], data[1]]) as u32 * 10; // 10 kHz units
                                                                              //
        // Horizontal active/blanking
        let h_active = ((data[4] as u16 & 0xF0) << 4) | data[2] as u16;
        let h_blanking = ((data[4] as u16 & 0x0F) << 8) | data[3] as u16;

        // Vertical active/blanking
        let v_active = ((data[7] as u16 & 0xF0) << 4) | data[5] as u16;
        let v_blanking = ((data[7] as u16 & 0x0F) << 8) | data[6] as u16;

        // Sync offsets and pulse widths
        let h_sync_offset = ((data[11] as u16 & 0xC0) << 2) | data[8] as u16;
        let h_sync_pulse_width = ((data[11] as u16 & 0x30) << 4) | data[9] as u16;
        let v_sync_offset = ((data[11] as u16 & 0x0C) << 2) | ((data[10] as u16 & 0xF0) >> 4);
        let v_sync_pulse_width = ((data[11] as u16 & 0x03) << 4) | (data[10] as u16 & 0x0F);

        // Image size
        let h_image_size = ((data[14] as u16 & 0xF0) << 4) | data[12] as u16;
        let v_image_size = ((data[14] as u16 & 0x0F) << 8) | data[13] as u16;

        // Borders
        let h_border = data[15];
        let v_border = data[16];

        // Features
        let interlaced = (data[17] & 0x80) != 0;

        // Stereo mode
        let stereo_bits = ((data[17] & 0x60) >> 4) | (data[17] & 0x01);
        let stereo_mode = match stereo_bits {
            0x00 | 0x01 => StereoMode::None,
            0x02 => StereoMode::FieldSequentialRight,
            0x04 => StereoMode::FieldSequentialLeft,
            0x03 => StereoMode::InterleavedRightEven,
            0x05 => StereoMode::InterleavedLeftEven,
            0x06 => StereoMode::FourWayInterleaved,
            0x07 => StereoMode::SideBySideInterleaved,
            _ => StereoMode::None, // Should not happen
        };

        // Sync type
        let sync_type = match (data[17] >> 3) & 0x03 {
            0x00 => SyncType::Analog {
                bipolar: (data[17] & 0x04) != 0,
                serration: (data[17] & 0x02) != 0,
                sync_on_rgb: (data[17] & 0x01) != 0,
            },
            0x02 => SyncType::DigitalComposite {
                serration: (data[17] & 0x04) != 0,
                hsync_positive: (data[17] & 0x02) != 0,
            },
            0x03 => SyncType::DigitalSeparate {
                vsync_positive: (data[17] & 0x04) != 0,
                hsync_positive: (data[17] & 0x02) != 0,
            },
            _ => SyncType::Analog {
                bipolar: false,
                serration: false,
                sync_on_rgb: false,
            },
        };

        DetailedTimingDescriptor {
            pixel_clock_khz: pixel_clock,
            horizontal_active: h_active,
            horizontal_blanking: h_blanking,
            vertical_active: v_active,
            vertical_blanking: v_blanking,
            horizontal_sync_offset: h_sync_offset,
            horizontal_sync_pulse_width: h_sync_pulse_width,
            vertical_sync_offset: v_sync_offset,
            vertical_sync_pulse_width: v_sync_pulse_width,
            horizontal_image_size_mm: h_image_size,
            vertical_image_size_mm: v_image_size,
            horizontal_border: h_border,
            vertical_border: v_border,
            features: TimingFeatures {
                interlaced,
                stereo_mode,
                sync_type,
            },
        }
    }
}

/// Error type for EDID parsing
#[derive(Debug)]
pub enum EdidError {
    InvalidChecksum,
    InvalidHeaderPattern,
}

// A simple example of how to use the parser
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_edid_parse() {
        // Example EDID data from Tiliqua screen
        let edid_data = [
            0x0, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x0,
            0xff, 0xff, 0x32, 0x31, 0x45, 0x6, 0x0, 0x0,
            0xc, 0x1c, 0x1, 0x3, 0x80, 0xf, 0xa, 0x78,
            0xa, 0xd, 0xc9, 0xa0, 0x57, 0x47, 0x98, 0x27,
            0x12, 0x48, 0x4c, 0x0, 0x0, 0x0, 0x1, 0xc1,
            0x1, 0x1, 0x1, 0xc1, 0x1, 0x1, 0x1, 0x1,
            0x1, 0x1, 0x1, 0x1, 0x1, 0x1, 0x9b, 0xe,
            0xd0, 0x64, 0x20, 0xd0, 0x28, 0x20, 0x28, 0x14,
            0x84, 0x4, 0xd0, 0xd0, 0x22, 0x0, 0x0, 0x1e,
            0x9c, 0xe, 0xd0, 0x64, 0x20, 0xd0, 0x28, 0x20,
            0x14, 0x28, 0x48, 0x1, 0x5, 0x28, 0x0, 0x20,
            0x20, 0x20, 0x0, 0x0, 0x0, 0xfa, 0x0, 0xa,
            0x20, 0x20, 0x20, 0x20, 0x2, 0x0, 0x20, 0x20,
            0x20, 0x20, 0x20, 0xa, 0x0, 0x0, 0x0, 0xfc,
            0x0, 0x5a, 0x4c, 0x37, 0x32, 0x30, 0x58, 0x37,
            0x32, 0x30, 0xa, 0x20, 0x20, 0x20, 0x1, 0x62,
        ];

        let edid = Edid::parse(&edid_data);
        match edid {
            Ok(data) => log::info!("Successfully parsed EDID: {data:#?}"),
            Err(e) => panic!("Failed to parse EDID: {e:?}"),
        }
    }
}
