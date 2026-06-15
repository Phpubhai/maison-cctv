// Cross-check CCTV staff faces against maisonPOS face profiles.
//
// Answers: "is staff_01 / staff_02 the same person as some therapistId?"
// by running the SAME model the POS used (@vladmandic/human faceres) on the
// CCTV face crops and applying the calibrated similarity from the export
// spec (1:N threshold 0.55 + margin over the runner-up).
//
// WHERE TO RUN: on the machine with the maisonPOS repo (it already has
// @vladmandic/human). This CCTV machine has no Node runtime.
//
//   1. copy this file into the maisonPOS repo (e.g. scripts/)
//   2. copy the CCTV face crops there too:  Yolo_Monitor/faces/*.jpg
//   3. npm i -D @tensorflow/tfjs-node        (one-time; human's node backend)
//   4. node scripts/face_crosscheck.mjs <faces-dir> <face-profiles.json>
//
// Output: best therapistId per staff jpg with similarity + margin. Edit
// Yolo_Monitor/staff.json on the CCTV machine yourself — this tool never
// writes anything (a wrong match would route penalties to the wrong person).
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import * as tf from '@tensorflow/tfjs-node'
import { Human } from '@vladmandic/human/dist/human.node.js'

const [facesDir, profilesPath] = process.argv.slice(2)
if (!facesDir || !profilesPath) {
    console.error('usage: node face_crosscheck.mjs <faces-dir> <face-profiles.json>')
    process.exit(1)
}

const THRESHOLD = 0.55   // 1:N decision threshold (spec recommendation)
const MARGIN = 0.05      // best must beat the runner-up by this much

// ── similarity: exact port of the export spec / human match.ts ────────────────
function descriptorSimilarity(a, b) {
    if (!a?.length || a.length !== b?.length) return 0
    const dist = Math.round(25 * a.reduce((s, x, i) => s + (x - b[i]) ** 2, 0) * 100) / 100
    if (dist === 0) return 1
    const norm = (1 - Math.sqrt(dist) / 100 - 0.2) / 0.6
    return Math.round(Math.max(0, Math.min(1, norm)) * 100) / 100
}

const profiles = JSON.parse(readFileSync(profilesPath, 'utf8'))
console.log(`${profiles.length} therapist profile(s) loaded`)

const human = new Human({
    modelBasePath: 'https://vladmandic.github.io/human/models',
    face: {
        enabled: true,
        detector: { minConfidence: 0.2, maxDetected: 1 }, // CCTV crops are rough
        description: { enabled: true },
    },
    body: { enabled: false }, hand: { enabled: false }, gesture: { enabled: false },
})
await human.load()

const jpgs = readdirSync(facesDir).filter(f => /\.(jpe?g|png)$/i.test(f))
for (const file of jpgs) {
    const tensor = tf.node.decodeImage(readFileSync(join(facesDir, file)), 3)
    const res = await human.detect(tensor)
    tf.dispose(tensor)
    const desc = res.face?.[0]?.embedding
    if (!desc?.length) {
        console.log(`\n${file}: NO FACE / NO DESCRIPTOR (crop too blurry?) -> re-capture a sharper crop`)
        continue
    }
    const scored = profiles
        .map(p => ({
            therapistId: p.therapistId,
            sim: Math.max(...p.embeddings.map(e => descriptorSimilarity(desc, e)), 0),
        }))
        .sort((a, b) => b.sim - a.sim)
    const [best, runner] = scored
    const margin = best.sim - (runner?.sim ?? 0)
    const verdict = best.sim >= THRESHOLD && margin >= MARGIN ? 'MATCH'
        : best.sim >= THRESHOLD ? 'AMBIGUOUS (margin too small)' : 'NO MATCH'
    console.log(`\n${file}:`)
    scored.slice(0, 3).forEach(s => console.log(`  ${s.therapistId}  sim ${s.sim.toFixed(2)}`))
    console.log(`  -> ${verdict}${verdict === 'MATCH' ? `: therapist_id = ${best.therapistId}` : ''}`)
}
console.log('\nIf a crop fails or is ambiguous: capture a sharper head crop from the')
console.log('CCTV (enroll_face.py watch), or map that person manually in staff.json.')
process.exit(0)
