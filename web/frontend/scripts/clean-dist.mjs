import { rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptsDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptsDir, '..')
const distRoot = path.join(frontendRoot, 'dist')

if (path.dirname(distRoot) !== frontendRoot) {
  throw new Error('refusing to clean a build directory outside the frontend root')
}

await rm(distRoot, { recursive: true, force: true })
