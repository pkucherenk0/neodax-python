/**
 * Runs before every test session: mints + funds FRESH throwaway accounts via the Python
 * arrangement script, writing .arrangement.json. Automatic on purpose -- a manually-run,
 * reused arrangement accumulates leftover orders/positions on the same account across runs
 * ("trash"). Every run starts from a clean, newly-minted account.
 */
import { execFileSync } from 'node:child_process';
import path from 'node:path';

export default function globalSetup(): void {
  const repoRoot = path.resolve(__dirname, '..');
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  execFileSync(python, ['tools/arrange_metamask_e2e.py'], { cwd: repoRoot, stdio: 'inherit' });
}
