const test = require('node:test');
const assert = require('node:assert/strict');

const { shouldRetryWithoutOrientation } = require('../app/services/slice/input-processing');

test('a failed slice of an oriented file is retried on the upload', () => {
    // 2026-10-19: PrusaSlicer crashed (std::bad_alloc) on the re-exported
    // oriented STL while the uploaded file sliced fine.
    const err = Object.assign(new Error('Command failed: prusa-slicer'), { stderr: 'std::bad_alloc' });
    assert.equal(shouldRetryWithoutOrientation(err, 'in_oriented.stl', 'in.stl'), true);
});

test('no retry when the upload itself was sliced', () => {
    assert.equal(shouldRetryWithoutOrientation(new Error('boom'), 'in.stl', 'in.stl'), false);
    assert.equal(shouldRetryWithoutOrientation(new Error('boom'), 'in.stl', null), false);
});

test('timeouts are not retried', () => {
    const err = Object.assign(new Error('timed out'), { killed: true });
    assert.equal(shouldRetryWithoutOrientation(err, 'in_oriented.stl', 'in.stl'), false);
});
