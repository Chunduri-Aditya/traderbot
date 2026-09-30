export const meta = {
  name: 'traderbot-attack',
  description: 'Attack or audit one traderbot phase: attackers write failing tests in the scratchpad, Fable verifiers keep only reproducible reds',
  whenToUse: 'traderbot Phase 2, 3 and 5 exits (attack) and Phase 7 (audit), only on Aditya\'s yes',
  phases: [{ title: 'Attack' }, { title: 'Verify', model: 'fable' }, { title: 'Audit', model: 'fable' }],
}

const A = args  // {mode, phase, repo, scratch, head, spec, runbook, rules, known_limits[], live, lenses[{key, focus, files[], leads[]}]}

const CASE = { type: 'object', required: ['id', 'test_name', 'claim', 'spec_quote', 'expected_failure', 'severity'],
  properties: { id: { type: 'string' }, test_name: { type: 'string' }, claim: { type: 'string' },
    spec_quote: { type: 'string' }, expected_failure: { type: 'string' },
    severity: { type: 'string', enum: ['blocking', 'risk', 'spec_gap'] } } }
const ATTACK = { type: 'object', required: ['lens', 'file', 'cases', 'run'],
  properties: { lens: { type: 'string' }, file: { type: 'string' }, cases: { type: 'array', items: CASE },
    run: { type: 'object', required: ['command', 'exit_code', 'summary'],
      properties: { command: { type: 'string' }, exit_code: { type: 'integer' }, summary: { type: 'string' } } } } }
const VERDICT = { type: 'object', required: ['lens', 'head_ok', 'tree_clean', 'verdicts'],
  properties: { lens: { type: 'string' }, head_ok: { type: 'boolean' }, tree_clean: { type: 'boolean' },
    verdicts: { type: 'array', items: { type: 'object',
      required: ['id', 'test_name', 'confirmed', 'reason', 'command', 'exit_code', 'observed', 'quote_found', 'reran_red'],
      properties: { id: { type: 'string' }, test_name: { type: 'string' }, confirmed: { type: 'boolean' },
        reason: { type: 'string' }, command: { type: 'string' }, exit_code: { type: 'integer' },
        observed: { type: 'string' }, quote_found: { type: 'boolean' }, reran_red: { type: 'boolean' } } } } } }
const FINDINGS = { type: 'object', required: ['lens', 'findings'],
  properties: { lens: { type: 'string' }, findings: { type: 'array', items: { type: 'object',
    required: ['tier', 'file', 'line', 'summary', 'evidence'],
    properties: { tier: { type: 'string', enum: ['blocking', 'risk', 'spec_gap', 'nit'] }, file: { type: 'string' },
      line: { type: 'integer' }, summary: { type: 'string' }, evidence: { type: 'string' } } } } } }

const rules = () => [
  `Repo: ${A.repo} at HEAD ${A.head}. Spec: ${A.spec}. Runbook (its Deltas table wins): ${A.runbook}. Read the ${A.phase} sections of both, and ${A.rules}.`,
  'Never edit, create or delete anything under the repo. Never run scripts/mutate.py (it rewrites gate.py), `-m live`, `traderbot propose`, `traderbot approve`, anything that reads the Keychain, or anything that touches ~/.traderbot.',
  `Live spend allowed: ${A.live}.`,
  `Known limits, not findings: ${A.known_limits.join(' | ')}`,
].join('\n')

const attackPrompt = l => `${rules()}

You attack one lens of traderbot ${A.phase}: ${l.focus}
Start with: ${l.files.join(', ')}. Leads to test (unverified; do not stop at them): ${l.leads.join(' | ')}
Your folder: ${A.scratch}/${l.key}/ already holds copies of tests/conftest.py and tests/support.py, so the offline tripwires (claude, Keychain, HTTP) and TRADERBOT_HOME=tmp_path apply. Write every case into ${A.scratch}/${l.key}/test_attack_${l.key}.py, one pytest function per broken guarantee, each with a docstring quoting, word for word, the spec, runbook, RULES.md or ARCHITECTURE.md line it holds the code to, and an expected value taken from that line, never computed by the code under test. A subprocess case must set TRADERBOT_HOME to its tmp_path and must not reach a working terminal.
Every case must FAIL at HEAD for the reason you claim (an assertion, or the exception your claim names), not by ImportError, NameError, a fixture error or a Tripwire, unless reaching a guarded call is the claim. Run: cd ${A.repo} && uv run pytest -p no:cacheprovider -q ${A.scratch}/${l.key}/ and report the command, exit code and summary line. Zero cases is an honest answer; at most 8.`

const verifyPrompt = (l, found) => `${rules()}

You independently verify another agent's failing tests for traderbot ${A.phase}, lens ${l.key}. Default to not confirmed when unsure.
First: git -C ${A.repo} rev-parse HEAD must equal ${A.head}; git -C ${A.repo} status --short --untracked-files=all must be empty. Report both.
File: ${found.file}. Claimed cases: ${JSON.stringify(found.cases)}
For each case run cd ${A.repo} && uv run pytest -p no:cacheprovider -q "${found.file}::<test_name>" and confirm only if all hold: it fails at HEAD; the failure is the claimed one (read the traceback, not the test name); spec_quote occurs verbatim in the spec, the runbook, RULES.md or ARCHITECTURE.md (search the files for it); the expected value comes from that quote; the case does not patch traderbot internals into a state production code cannot reach; it is not a known limit; a second run is red again. Copy the last lines of the failure verbatim into observed.`

const auditPrompt = l => `${rules()}

Fresh context audit of traderbot, lens ${l.key}: ${l.focus}. Read only; run tests only offline. Every finding carries file:line and evidence (a command with its output, or a quoted line). Tiers: blocking, risk, spec_gap, nit.`

if (A.mode === 'audit') {
  phase('Audit')
  const rows = await parallel(A.lenses.map(l => () =>
    agent(auditPrompt(l), { label: `audit:${l.key}`, phase: 'Audit', agentType: 'reviewer', model: 'fable', schema: FINDINGS })))
  return { phase: A.phase, head: A.head, rows }
}

const rows = await pipeline(
  A.lenses,
  l => agent(attackPrompt(l), { label: `attack:${l.key}`, phase: 'Attack', schema: ATTACK }),
  (found, l) => (found && found.cases.length)
    ? agent(verifyPrompt(l, found), { label: `verify:${l.key}`, phase: 'Verify', model: 'fable', schema: VERDICT })
        .then(v => ({ lens: l.key, found, verdicts: v }))
    : { lens: l.key, found, verdicts: null },
)
return { phase: A.phase, head: A.head, rows }
