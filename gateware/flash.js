import { runOpenFPGALoader, Exit } from 'https://cdn.jsdelivr.net/npm/@yowasp/openfpgaloader/gen/bundle.js';
import { Archive } from 'https://cdn.jsdelivr.net/npm/libarchive.js@2.0.2/+esm';

// Constants from the Python script
const BOOTLOADER_BITSTREAM_ADDR = 0x000000;
const SLOT_BITSTREAM_BASE = 0x100000;
const SLOT_SIZE = 0x100000;
const MANIFEST_SIZE = 1024;
const FIRMWARE_BASE_SLOT0 = 0x1C0000;
const FLASH_PAGE_SIZE = 1024;
const MAX_ERROR_LINES = 100;
const globalLogHistory = [];

// Store loaded archives
const loadedArchives = new Map();

// Add this to store message history for each slot
const messageHistory = new Map();

// Make handleFlash global
window.handleFlash = async function(slotId) {
    const slotData = loadedArchives.get(slotId.toString());
    if (!slotData) return;
    
    try {
        // Show manifest contents first
        showError(slotId, "Manifest contents to be flashed:");
        showError(slotId, JSON.stringify(slotData.manifest, null, 2));
        
        // Log regions
        showError(slotId, "\nRegions to be flashed:");
        for (const region of slotData.regions.sort((a, b) => a.addr - b.addr)) {
            const alignedSize = (region.size + FLASH_PAGE_SIZE - 1) & ~(FLASH_PAGE_SIZE - 1);
            showError(slotId, `${region.name}:
    start: 0x${region.addr.toString(16)}
    end:   0x${(region.addr + alignedSize - 1).toString(16)}
    size:  ${region.size} bytes (${alignedSize} bytes aligned)`);
        }

        // Collect and show commands
        const commands = [];
        for (const region of slotData.regions) {
            const fileData = await slotData.archive.get(region.filename)?.arrayBuffer();
            if (fileData) {
                commands.push({
                    args: [
                        "-c", "dirtyJtag",
                        "-f",
                        "-o", `${region.addr.toString(16)}`,
                        "--file-type", region.filename.endsWith('.bit') ? "bit" : "raw"
                    ],
                    data: new Uint8Array(fileData),
                    name: region.filename,
                    addr: region.addr
                });
            }
        }

        showError(slotId, "\nThe following commands will be executed:\n" + 
            commands.map(cmd => 
                `openFPGALoader ${cmd.args.join(' ')} ${cmd.name}`
            ).join('\n')
        );

        // Execute commands
        for (const cmd of commands) {
            try {
                // Create a virtual file for the data
                const filesIn = {
                    'data': cmd.data
                };
                
                // Add the filename to the args
                const args = [...cmd.args, 'data'];
                
                // Run openFPGALoader with the virtual file
                await runOpenFPGALoader(args, filesIn, {
                    stdout: (data) => {
                        if (data) {
                            const text = new TextDecoder().decode(data);
                            showError(slotId, text);
                        }
                    },
                    stderr: (data) => {
                        if (data) {
                            const text = new TextDecoder().decode(data);
                            showError(slotId, `stderr: ${text}`);
                        }
                    }
                });
            } catch (error) {
                if (error instanceof Exit) {
                    throw new Error(`Command failed with exit code ${error.code}`);
                }
                throw error;
            }
        }
        
        showError(slotId, "Flash completed successfully!");
    } catch (error) {
        showError(slotId, `Flash failed: ${error.message}`);
    }
};

// Initialize all event handlers after DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // File input handlers
    document.querySelectorAll('input[type="file"]').forEach(input => {
        input.addEventListener('change', async (e) => {
            const slotId = e.target.id.split('-')[1];
            const file = e.target.files[0];
            if (file) {
                try {
                    await loadArchive(file, slotId);
                    // Fix bootloader button selection
                    const button = slotId === 'bootloader' 
                        ? document.querySelector('#bootloader button')
                        : document.querySelector(`#slot-${slotId} button`);
                    if (button) {
                        button.disabled = false;
                    }
                } catch (error) {
                    showError(slotId, error.message);
                }
            }
        });
    });

    // Flash button handlers
    document.querySelectorAll('.flash-button').forEach(button => {
        button.addEventListener('click', () => {
            // Fix bootloader slot ID handling
            const slot = button.closest('.slot');
            const slotId = slot.id === 'bootloader' ? 'bootloader' : slot.id.replace('slot-', '');
            handleFlash(slotId);
        });
    });
});

// Add this helper class at the top of the file
class TarArchive {
    constructor() {
        this.files = new Map();
    }

    set(name, data) {
        this.files.set(name, data);
    }

    get(name) {
        const data = this.files.get(name);
        if (!data) return null;
        
        return {
            arrayBuffer: () => Promise.resolve(data),
            text: () => Promise.resolve(new TextDecoder().decode(data))
        };
    }
}

async function loadArchive(file, slotId) {
    const archive = await readTarGz(file);
    
    // Parse manifest
    const manifest = JSON.parse(await archive.get('manifest.json').text());
    
    // Check for XIP/bootloader images in normal slots
    const isBootloader = slotId === 'bootloader';
    if (!isBootloader) {
        // Check if any region has psram_dst null/undefined (indicates XIP/bootloader firmware)
        const hasXipRegions = (manifest.regions || []).some(region => 
            region.filename && (region.psram_dst === null || region.psram_dst === undefined)
        );
        if (hasXipRegions) {
            throw new Error("Cannot load bootloader/XIP firmware in a normal slot - all regions must have psram_dst set");
        }
    } else {
        // Check if any region has psram_dst set (indicates PSRAM-based firmware)
        const hasPsramRegions = (manifest.regions || []).some(region => 
            region.filename && region.psram_dst !== undefined && region.psram_dst !== null
        );
        if (hasPsramRegions) {
            throw new Error("Cannot load non-XIP / user bitstream in bootloader slot - no regions should have psram_dst set");
        }
    }
    
    // Collect regions
    const regions = [];
    const slot = isBootloader ? null : parseInt(slotId);
    
    if (isBootloader) {
        // XIP firmware handling
        regions.push({
            name: 'bootloader bitstream',
            filename: 'top.bit',
            addr: BOOTLOADER_BITSTREAM_ADDR,
            size: (await archive.get('top.bit').arrayBuffer()).byteLength,
        });
        
        // Add XIP firmware regions
        for (const region of manifest.regions || []) {
            if (region.filename) {
                regions.push({
                    name: `firmware '${region.filename}'`,
                    filename: region.filename,
                    addr: region.spiflash_src,
                    size: region.size,
                });
            }
        }
    } else {
        // Regular slot handling
        const slotBase = SLOT_BITSTREAM_BASE + (slot * SLOT_SIZE);
        const firmwareBase = FIRMWARE_BASE_SLOT0 + (slot * SLOT_SIZE);
        
        regions.push({
            name: 'bitstream',
            filename: 'top.bit',
            addr: slotBase,
            size: (await archive.get('top.bit').arrayBuffer()).byteLength,
        });
        
        let currentFirmwareBase = firmwareBase;
        for (const region of manifest.regions || []) {
            if (region.filename && region.psram_dst !== undefined) {
                // Update spiflash_src in manifest
                region.spiflash_src = currentFirmwareBase;
                
                regions.push({
                    name: region.filename,
                    filename: region.filename,
                    addr: currentFirmwareBase,
                    size: region.size,
                });
                currentFirmwareBase += region.size;
                currentFirmwareBase = (currentFirmwareBase + 0xFFF) & ~0xFFF;
            }
        }

        // Add manifest region last (with updated spiflash_src values)
        const manifestJson = JSON.stringify(manifest);
        archive.set('manifest.json', new TextEncoder().encode(manifestJson));
        
        regions.push({
            name: 'manifest',
            filename: 'manifest.json',
            addr: (slotBase + SLOT_SIZE) - MANIFEST_SIZE,
            size: MANIFEST_SIZE,
        });
    }
    
    // Check for overlaps
    const hasOverlap = checkRegionOverlaps(regions, slot);
    if (hasOverlap[0]) {
        throw new Error(hasOverlap[1]);
    }
    
    // Store archive and regions
    loadedArchives.set(slotId, { archive, regions, manifest });
    
    // Display manifest
    displayRegions(slotId, regions, manifest);
}

function checkRegionOverlaps(regions, slot) {
    const alignedRegions = [];
    
    for (const r of regions) {
        const start = r.addr;
        const size = (r.size + FLASH_PAGE_SIZE - 1) & ~(FLASH_PAGE_SIZE - 1);
        const end = start + size;
        alignedRegions.push([start, end, r.name]);
        
        if (slot !== null) {
            const slotStart = Math.floor(start / SLOT_SIZE) * SLOT_SIZE;
            const slotEnd = slotStart + SLOT_SIZE;
            if (end > slotEnd) {
                return [true, `Region '${r.name}' exceeds slot boundary: ends at 0x${end.toString(16)}, slot ends at 0x${slotEnd.toString(16)}`];
            }
        }
    }
    
    alignedRegions.sort((a, b) => a[0] - b[0]);
    
    for (let i = 0; i < alignedRegions.length - 1; i++) {
        const currEnd = alignedRegions[i][1];
        const nextStart = alignedRegions[i + 1][0];
        if (currEnd > nextStart) {
            return [true, `Overlap detected between '${alignedRegions[i][2]}' (ends at 0x${currEnd.toString(16)}) and '${alignedRegions[i + 1][2]}' (starts at 0x${nextStart.toString(16)})`];
        }
    }
    
    return [false, ""];
}

function displayRegions(slotId, regions, manifest) {
    const regionsDiv = document.getElementById(`regions-${slotId}`);
    
    function createTableFromObject(obj) {
        const rows = [];
        function addRow(key, value, indent = 0) {
            // Skip the regions array
            if (key === 'regions') return;
            
            if (typeof value === 'object' && value !== null) {
                Object.entries(value).forEach(([k, v]) => {
                    addRow(k, v, indent + 2);
                });
            } else {
                rows.push(`<tr><td>${'&nbsp;'.repeat(indent)}${key}</td><td>${value}</td></tr>`);
            }
        }
        
        Object.entries(manifest).forEach(([key, value]) => {
            addRow(key, value);
        });
        
        return `<table class="manifest-table">${rows.join('')}</table>`;
    }
    
    regionsDiv.innerHTML = createTableFromObject(manifest);
}

function addToGlobalLog(slotId, message, type = 'info') {
    const timestamp = new Date().toLocaleTimeString();
    const lines = message.split('\n');
    
    lines.forEach(line => {
        if (line.trim()) {  // Skip empty lines
            const logEntry = {
                timestamp,
                slotId,
                message: line,
                type
            };
            globalLogHistory.push(logEntry);
            
            // Update display
            const logContent = document.getElementById('global-log');
            const logLine = document.createElement('div');
            logLine.className = `log-line ${type}`;
            logLine.innerHTML = `<span class="timestamp">${timestamp}</span><span class="slot-id">[s/${slotId}] </span>${line}`;
            logContent.appendChild(logLine);
            
            // Auto-scroll to bottom
            logContent.scrollTop = logContent.scrollHeight;
        }
    });
}

function showError(slotId, message) {
    // Determine message type
    let type = 'info';
    if (message.toLowerCase().includes('error') || message.toLowerCase().includes('failed')) {
        type = 'error';
    } else if (message.toLowerCase().includes('success')) {
        type = 'success';
    }
    
    // Add to global log
    addToGlobalLog(slotId, message, type);
}

async function readTarGz(file) {
    // Create archive object for our use
    const archive = new TarArchive();
    
    // Read the file
    const fileData = await file.arrayBuffer();
    
    // Decompress gzip
    const inflated = pako.inflate(new Uint8Array(fileData));
    
    // Parse tar - process 512 byte blocks
    let offset = 0;
    while (offset < inflated.length) {
        // Read header block
        const header = inflated.slice(offset, offset + 512);
        
        // Check for end of archive (empty block)
        if (header.every(byte => byte === 0)) {
            break;
        }
        
        // Parse filename (100 bytes)
        const filename = new TextDecoder().decode(header.slice(0, 100)).split('\0')[0];
        
        // Parse file size (12 bytes, octal string)
        const sizeStr = new TextDecoder().decode(header.slice(124, 136)).trim();
        const size = parseInt(sizeStr, 8);
        
        // Move past header
        offset += 512;
        
        // Read file content
        if (size > 0) {
            const content = inflated.slice(offset, offset + size);
            archive.set(filename, content);
            
            // Move to next 512-byte boundary
            offset += (Math.floor((size + 511) / 512) * 512);
        }
    }
    
    return archive;
} 