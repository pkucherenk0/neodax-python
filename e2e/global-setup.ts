/** Mints + funds fresh throwaway accounts via the Python arrangement script before every run,
 * writing .arrangement.json -- automatic so no run reuses a "trashed" account. */
import { execFileSync } from 'node:child_process';
import path from 'node:path';

export default function globalSetup(): void {
  const repoRoot = path.resolve(__dirname, '..');
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  execFileSync(python, ['tools/arrange_metamask_e2e.py'], { cwd: repoRoot, stdio: 'inherit' });
}
