#[derive(Clone, Copy)]
pub enum CcMapMode {
    Absolute,
}

#[derive(Clone, Copy)]
struct CcEntry {
    global_index: usize,
    mode: CcMapMode,
}

pub struct CcAction {
    pub global_index: usize,
    pub cc_value: u8,
    pub mode: CcMapMode,
}

pub struct MidiCcMapper {
    table: [Option<CcEntry>; 128],
}

impl Default for MidiCcMapper {
    fn default() -> Self {
        Self::new()
    }
}

impl MidiCcMapper {
    pub fn new() -> Self {
        Self { table: [None; 128] }
    }

    pub fn add(&mut self, cc: u8, global_index: usize, mode: CcMapMode) {
        self.table[cc as usize] = Some(CcEntry { global_index, mode });
    }

    pub fn process(&self, cc_num: u8, cc_val: u8) -> Option<CcAction> {
        self.table[cc_num as usize].map(|e| CcAction {
            global_index: e.global_index,
            cc_value: cc_val,
            mode: e.mode,
        })
    }
}
