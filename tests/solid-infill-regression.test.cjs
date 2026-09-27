const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { buildSlicerCommandArgs } = require('../app/services/slice/engine');
const { handleProcessingError } = require('../app/services/slice/errors');
const { parseOutputDetailed } = require('../app/services/slice/model-stats');

test('Prusa solid infill gets a compatible CLI pattern before config loading', () => {
    for (const infill of ['100%', '100']) {
        const args = buildSlicerCommandArgs('FDM', 'profile.ini', 'part.gcode', infill);
        assert.deepEqual(args.slice(-4), ['--fill-density', infill, '--fill-pattern', 'rectilinear']);
    }
    for (const infill of ['20%', '99%']) {
        const args = buildSlicerCommandArgs('FDM', 'profile.ini', 'part.gcode', infill);
        assert.equal(args.includes('--fill-pattern'), false);
    }
});

test('solid pattern failures have a stable non-retryable response', () => {
    let status;
    let body;
    const response = {
        status(value) { status = value; return this; },
        json(value) { body = value; return this; }
    };
    handleProcessingError(
        { message: 'PrusaSlicer failed', stderr: 'error: The selected fill pattern is not supposed to work at 100% density' },
        response,
        [],
        'part.stl',
        () => '.stl'
    );
    assert.equal(status, 422);
    assert.equal(body.errorCode, 'INVALID_INFILL_PATTERN');
});

test('detailed G-code estimate takes priority over rounded M73 minutes', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'slicer-time-'));
    const file = path.join(dir, 'part.gcode');
    try {
        fs.writeFileSync(file, 'M73 P0 R12\n; estimated printing time (normal mode) = 12m 41s\n; filament used [mm] = 1000\n');
        const stats = await parseOutputDetailed(file, 'FDM', 0.2, 20);
        assert.equal(stats.print_time_seconds, 761);
        assert.equal(stats.material_used_m, 1);
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
});
