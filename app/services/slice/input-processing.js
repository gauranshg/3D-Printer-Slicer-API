/**
 * Input conversion and orientation pipeline helpers.
 */

const fs = require('node:fs');
const path = require('node:path');
const { EXTENSIONS } = require('../../config/constants');
const { PYTHON_EXECUTABLE } = require('../../config/python');
const { runCommand } = require('./command');

/**
 * Convert supported non-STL inputs to STL for downstream slicing.
 * @param {string} processableFile Source file path.
 * @param {string[]} filesCleanupList Temporary file collector.
 * @returns {Promise<string>} Final STL-compatible file path.
 */
async function convertInputToStl(processableFile, filesCleanupList) {
    const currentExt = path.extname(processableFile).toLowerCase();
    let finalStlPath = processableFile;

    if (['.obj', '.3mf', '.ply'].includes(currentExt)) {
        console.log('[INFO] Converting Mesh to STL...');
        finalStlPath = processableFile + '.stl';
        filesCleanupList.push(finalStlPath);
        await runCommand(PYTHON_EXECUTABLE, ['mesh2stl.py', processableFile, finalStlPath]);
        return finalStlPath;
    }

    if (EXTENSIONS.cad.includes(currentExt)) {
        console.log('[INFO] Converting CAD to STL...');
        finalStlPath = processableFile + '.stl';
        filesCleanupList.push(finalStlPath);
        await runCommand(PYTHON_EXECUTABLE, ['cad2stl.py', processableFile, finalStlPath]);
        return finalStlPath;
    }

    return finalStlPath;
}

/**
 * Whether uploads of this technology are re-oriented before slicing.
 *
 * FDM quotes keep the uploaded pose (2026-11-05). The "stable pose" search
 * laid upright models on their side (an 18 cm bust sliced at 10 cm tall),
 * adding supports and ~20% print time versus how the owner prints the same
 * file in Bambu Studio, which keeps the imported orientation.
 * @param {'FDM'|'SLA'} technology Active technology mode.
 * @returns {boolean} True when orientation optimization should run.
 */
function shouldOptimizeOrientation(technology) {
    return technology !== 'FDM';
}

/**
 * Attempt orientation optimization and fall back to original file on failure.
 * FDM uploads are returned untouched (see shouldOptimizeOrientation).
 * @param {string} processableFile STL input path.
 * @param {'FDM'|'SLA'} technology Active technology mode.
 * @param {string[]} filesCleanupList Temporary file collector.
 * @returns {Promise<string>} Optimized or original STL path.
 */
async function tryOptimizeOrientation(processableFile, technology, filesCleanupList) {
    if (!shouldOptimizeOrientation(technology)) {
        console.log(`[INFO] Keeping uploaded orientation for ${technology}.`);
        return processableFile;
    }
    console.log(`[INFO] Optimizing orientation for ${technology}...`);
    const orientedStlPath = processableFile.replace('.stl', '_oriented.stl');

    try {
        await runCommand(PYTHON_EXECUTABLE, ['orient.py', processableFile, orientedStlPath, technology]);
        if (fs.existsSync(orientedStlPath)) {
            filesCleanupList.push(orientedStlPath);
            return orientedStlPath;
        }
    } catch (error_) {
        console.warn(`[WARN] Orientation optimization failed, proceeding with original. Error: ${error_.message}`);
    }

    return processableFile;
}

/**
 * Decide whether a failed slice deserves one more try on the unrotated model.
 * Re-exported (rotated) STLs have crashed PrusaSlicer where the upload sliced
 * fine (2026-10-19, std::bad_alloc), so a failure on an oriented file is not
 * proof the model is bad. Timeouts are not retried: a second run would double
 * an already maximal wait.
 * @param {Error & {killed?: boolean}} err Slicer failure.
 * @param {string} slicedFile File the failed slice ran on.
 * @param {string} unorientedFile Model before orientation optimization.
 * @returns {boolean} True when slicing unorientedFile is worth trying.
 */
function shouldRetryWithoutOrientation(err, slicedFile, unorientedFile) {
    if (!unorientedFile || slicedFile === unorientedFile) return false;
    if (err?.killed) return false;
    return true;
}

module.exports = {
    convertInputToStl,
    shouldOptimizeOrientation,
    tryOptimizeOrientation,
    shouldRetryWithoutOrientation
};
