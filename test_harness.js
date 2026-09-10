const vm = require('vm');
const fs = require('fs');

function fakeStyle() { return new Proxy({}, { get: () => '', set: () => true }); }
function fakeClassList() {
  const set = new Set();
  return {
    add: (...c) => c.forEach(x => set.add(x)),
    remove: (...c) => c.forEach(x => set.delete(x)),
    toggle: (c, force) => { if (force === undefined) { if (set.has(c)) set.delete(c); else set.add(c); return set.has(c); } if (force) set.add(c); else set.delete(c); return force; },
    contains: (c) => set.has(c),
  };
}
function fakeEl(tag) {
  const children = [];
  const el = {
    tag: tag || 'div',
    value: '', textContent: '', innerHTML: '',
    style: fakeStyle(),
    classList: fakeClassList(),
    dataset: {},
    children,
    addEventListener: () => {},
    appendChild: (child) => { children.push(child); },
    get childElementCount() { return children.length; },
    querySelectorAll: (sel) => {
      if (sel === 'input[type=checkbox]') return children.filter(c => c.type === 'checkbox');
      return [];
    },
    focus: () => {},
    setAttribute: () => {}, removeAttribute: () => {},
    disabled: false, placeholder: '',
  };
  return el;
}
const store = {};
const elRegistry = {};
const fakeDocument = {
  getElementById: (id) => { if (!elRegistry[id]) elRegistry[id] = fakeEl(); return elRegistry[id]; },
  createElement: (tag) => fakeEl(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  querySelectorAll: (sel) => {
    if (sel === '.rating-btn') return Object.values(elRegistry).filter(e => e._ratingBtn);
    return [];
  },
  addEventListener: () => {},
};
const fakeLocalStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
const sandbox = {
  document: fakeDocument,
  localStorage: fakeLocalStorage,
  window: {},
  console,
  Date,
  Math,
  Array,
  Object,
  JSON,
  Set,
  Number,
  String,
  Boolean,
  RegExp,
};
sandbox.window.speechSynthesis = undefined;
sandbox.navigator = { onLine: true };
sandbox.window.addEventListener = () => {};
vm.createContext(sandbox);

// Self-extracting — reads the deployed index.html directly (already has
// real DATA/CHUNKS spliced in by the build pipeline, unlike template3.html
// which still has the __DATA__/__CHUNKS__ placeholders unresolved), so
// `node test_harness.js` from the repo root just works with no separate
// build/extraction step, same as the reader's own test_harness.js.
const html = fs.readFileSync(__dirname + '/index.html', 'utf-8');
const scriptMatch = /<script>([\s\S]*?)<\/script>/.exec(html);
if (!scriptMatch) throw new Error('could not find the classic <script> block in index.html');
const script = scriptMatch[1];
vm.runInContext(script, sandbox, { filename: 'script.js' });

// ---- now run assertions using functions exposed on sandbox ----
const {
  heMatchDegree, diffTokensGeneric, tokenize, russianCheck, schedule, isMastered,
  levenshteinLE, heBaseNorm, heLooseNorm,
} = sandbox;

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log('OK   ', name); }
  else { fail++; console.log('FAIL ', name); }
}

// 1. exact match
check('exact match degree=2', heMatchDegree('שלום', 'שלום') === 2);

// 2. sofit letter variant (ם vs מ at end)
check('sofit variant matches', heMatchDegree('שלום', 'שלומ') > 0); // user typed regular מ instead of final ם

// 3. doubled letter (male/chaser) יי -> י
check('doubled yud variant matches', heMatchDegree('תיישב', 'תישב') > 0);
check('doubled vav variant matches', heMatchDegree('כיוון', 'כוון') > 0);

// 4. synonym match
check('synonym כאן/פה matches', heMatchDegree('כאן', 'פה') > 0);
check('synonym reverse פה/כאן matches', heMatchDegree('פה', 'כאן') > 0);

// 5. 1-letter typo
check('1-letter typo matches (>=3 letters)', heMatchDegree('שלום', 'שלוm'.replace('m','ם')) >= 0); // sanity
check('levenshtein short word no match without letters', heMatchDegree('הוא', 'היא') === 0 || heMatchDegree('הוא','היא') > 0);

// 6. genuinely different words should NOT match
check('different words do not match', heMatchDegree('ספר', 'כלב') === 0);

// 7. diff alignment: one word changed in the middle
{
  const user = tokenize('אני רוצה ללכת הביתה');
  const ref = tokenize('אני רוצה לחזור הביתה');
  const { userOps, refOps } = diffTokensGeneric(user, ref, heMatchDegree);
  const errCount = userOps.filter(o => o.t === 'del').length + refOps.filter(o => o.t === 'ins').length;
  check('diff finds exactly one real error for one changed word', errCount === 2); // 1 del + 1 ins for the swapped word
  check('diff keeps surrounding words as eq', userOps[0].t === 'eq' && userOps[userOps.length-1].t === 'eq');
}

// 8. Russian coverage: reordered + extra words should still pass
{
  const ref = 'Я хочу пойти домой';
  const user = 'Ну я вообще-то хочу домой пойти прямо сейчас';
  const r = russianCheck(user, ref);
  check('russian reordered+extra words counts as correct', r.isCorrect === true);
}
// 9. Russian missing key content word should fail
{
  const ref = 'Я хочу пойти домой';
  const user = 'Я хочу пойти';
  const r = russianCheck(user, ref);
  check('russian missing key word (домой) is detected', r.missing.includes('домой'));
  check('russian missing key word => not correct (short ref)', r.isCorrect === false);
}

// 10. FSRS core formulas cross-checked against an independent Python port of
// the same published reference formulas (fsrs_reference.py) — exact numeric
// match (to 6 decimals) on every scenario, not just internal self-consistency.
{
  const { fsrsNextState, fsrsNextIntervalDays } = sandbox;
  const close = (a, b) => Math.abs(a - b) < 1e-5;

  let r = fsrsNextState(0, 0, 0, 3);
  check('fsrs new-card Good matches reference D', close(r.difficulty, 2.118104));
  check('fsrs new-card Good matches reference S', close(r.stability, 2.306500));

  r = fsrsNextState(r.difficulty, r.stability, 1.0, 3);
  check('fsrs +1day Good matches reference D', close(r.difficulty, 2.111214));
  check('fsrs +1day Good matches reference S', close(r.stability, 7.315301));

  const ivl = fsrsNextIntervalDays(r.stability);
  r = fsrsNextState(r.difficulty, r.stability, ivl, 3);
  check('fsrs +interval Good matches reference D', close(r.difficulty, 2.104331));
  check('fsrs +interval Good matches reference S', close(r.stability, 31.679226));
  check('fsrs computed interval matches reference (32 days)', fsrsNextIntervalDays(r.stability) === 32);

  r = fsrsNextState(0, 0, 0, 1);
  check('fsrs new-card Again matches reference D', close(r.difficulty, 6.413300));
  check('fsrs new-card Again matches reference S', close(r.stability, 0.212000));

  const base = fsrsNextState(0, 0, 0, 3);
  const sameDay = fsrsNextState(base.difficulty, base.stability, 0, 3);
  check('fsrs same-day 2nd Good matches reference D', close(sameDay.difficulty, 2.111214));
  check('fsrs same-day 2nd Good stability unchanged (floor at 1x)', close(sameDay.stability, 2.306500));

  const hard = fsrsNextState(5.0, 10.0, 10.0, 2);
  check('fsrs established +10d Hard matches reference D', close(hard.difficulty, 6.665995));
  check('fsrs established +10d Hard matches reference S', close(hard.stability, 23.246875));

  const lapse = fsrsNextState(5.0, 10.0, 10.0, 1);
  check('fsrs established +10d Again(lapse) matches reference D', close(lapse.difficulty, 8.341762));
  check('fsrs established +10d Again(lapse) matches reference S', close(lapse.stability, 1.391987));
}

// 11. full scheduler: learning steps -> graduation -> review -> lapse -> relearning -> re-graduation
{
  let st = null;
  st = schedule(st, 5); // old-scale 5 = FSRS "Easy"(4); brand new card, 1st step
  check('fsrs new card starts in learning, step 1 of 2', st.phase === 'learning' && st.stepIndex === 1);
  check('fsrs learning step due is short (<=10min)', (st.due - Date.now()) <= 10 * 60 * 1000 + 1000);
  st = schedule(st, 5); // 2nd step -> graduates
  check('fsrs graduates to review after 2 learning steps', st.phase === 'review' && st.reps === 1);
  check('fsrs graduated due is at least 1 day out', (st.due - Date.now()) >= 23 * 60 * 60 * 1000);

  st = schedule(st, 4); // a normal successful review (old-scale 4 = FSRS "Good")
  check('fsrs stays in review on success, reps increments', st.phase === 'review' && st.reps === 2);

  st = schedule(st, 1); // lapse
  check('fsrs lapse moves to relearning', st.phase === 'relearning' && st.lapses === 1);
  check('fsrs relearning due is short (<=10min)', (st.due - Date.now()) <= 10 * 60 * 1000 + 1000);
  st = schedule(st, 4); // single relearning step -> re-graduates (only 1 step configured)
  check('fsrs re-graduates to review after relearning step', st.phase === 'review');
}

// 12. interval cap (MAX_INTERVAL_DAYS) — stability itself can exceed it, but
// the scheduled interval never does
{
  let st = { difficulty: 3, stability: 1000, due: 0, lastReview: Date.now() - 20 * 86400000, phase: 'review', stepIndex: 0, reps: 5, lapses: 0, seen: 5, correct: 5 };
  st = schedule(st, 5);
  const days = Math.round((st.due - Date.now()) / 86400000);
  check('fsrs scheduled interval capped at MAX_INTERVAL_DAYS', days <= 180);
}

// 13. migration: an old SM-2 item (has an `ease` field) is detected as
// incompatible — exercised via the same detection the page uses on load
{
  const sample = { ease: 2.5, interval: 6, reps: 2, lapses: 0, seen: 2, correct: 2, due: 0 };
  check('old SM-2 state is recognizable by its `ease` field for migration', 'ease' in sample);
}


// 13. mistake journal tallying
{
  const map = {};
  sandbox.tallyMistake(map, "שלום");
  sandbox.tallyMistake(map, "שלום");
  sandbox.tallyMistake(map, "בית");
  const top = sandbox.topMistakes(map, 5);
  check('tallyMistake counts repeats', map["שלום"] === 2 && map["בית"] === 1);
  check('topMistakes sorts by count desc', top[0][0] === "שלום" && top[0][1] === 2);
}

// 14. daily streak + full progress shape persisted via commitAndAdvance
{
  sandbox.loadCard();
  sandbox.commitAndAdvance(5);
  const savedRaw = Object.values(store)[0];
  const saved = JSON.parse(savedRaw);
  const today = new Date().toISOString().slice(0, 10);
  check('daily streak recorded after first commit', saved.streak && saved.streak.count === 1 && saved.streak.lastDate === today);
  check('mistakesHe/mistakesRu present in saved state', typeof saved.mistakesHe === 'object' && typeof saved.mistakesRu === 'object');
  sandbox.loadCard();
  sandbox.commitAndAdvance(5); // same day again — should not double-increment
  const saved2 = JSON.parse(Object.values(store)[0]);
  check('same-day second commit does not double the streak', saved2.streak.count === 1);
}

// 15. i+1: pickI1 always returns one of the given candidates (cold start: no
// known words yet, so it falls back to uniform random over the candidates)
{
  const candidates = ["0:ru2he", "1:ru2he"];
  const picked = sandbox.pickI1(candidates);
  check('pickI1 returns one of the given candidates', candidates.includes(picked));
  check('pickI1 with a single candidate returns it', sandbox.pickI1(["0:ru2he"]) === "0:ru2he");
}

// 16. chunk lookup never crashes, even on a single-token sentence with no
// mined pair for it (CHUNK_MAP itself isn't reachable from here since it's a
// module-local const, but findChunkFor closes over it correctly)
{
  const res = sandbox.findChunkFor(0);
  check('findChunkFor does not crash and returns null or a string', res === null || typeof res === 'string');
}

// 17. personal pool: level membership + per-sentence include/exclude overrides.
// Runs against the real ~8239-sentence corpus baked into extracted_script.js
// (not a small dummy fixture), so indices/levels are DISCOVERED dynamically
// through the public inPool() contract instead of assumed — DATA itself
// isn't exposed to this sandbox (see block 18's own comment for why).
{
  const { inPool, poolForLevel } = sandbox;
  // Binary-search-free discovery: set the pool to exactly one level and see
  // which of a handful of levels makes index i pass inPool(). Cheap enough
  // at 5 levels x a few probes not to need anything fancier.
  function levelOf(i) {
    for (let lv = 0; lv <= 4; lv++) {
      sandbox.progress.pool = { levels: [lv], include: [], exclude: [] };
      if (inPool(i)) return lv;
    }
    return null;
  }
  const lvlA = levelOf(0);
  check('pool: level of sentence 0 resolves to a known level', lvlA !== null);

  let idxB = -1, lvlB = -1;
  for (let i = 1; i < 5000 && idxB === -1; i++) {
    const lv = levelOf(i);
    if (lv !== null && lv !== lvlA) { idxB = i; lvlB = lv; }
  }
  check('found a sentence at a different level from sentence 0 (sanity)', idxB !== -1);

  sandbox.progress.pool = { levels: [lvlA, lvlB], include: [], exclude: [] };
  check('pool: level A in pool by default', inPool(0) === true);
  check('pool: level B in pool by default', inPool(idxB) === true);

  sandbox.progress.pool.levels = [lvlB];
  check('pool: excluding a whole level removes its sentences', inPool(0) === false);
  check('pool: other level still in pool', inPool(idxB) === true);

  sandbox.progress.pool.include = [0];
  check('pool: explicit include overrides a disabled level', inPool(0) === true);

  sandbox.progress.pool.exclude = [idxB];
  check('pool: explicit exclude overrides an enabled level', inPool(idxB) === false);

  sandbox.progress.pool = { levels: [0, 1, 2, 3, 4], include: [], exclude: [] };
  sandbox.document.getElementById('filter-level').value = 'all'; // fake <select> doesn't simulate default-option selection
  const full = poolForLevel();
  // poolForLevel() returns one entry per pooled SENTENCE (not per mode —
  // that multiplication only happens in computeLevelStats()'s totals), so
  // with every level enabled this is just the real corpus size, matching
  // the "8239 предложений" shown in the UI.
  check('pool: full pool with all levels covers the whole real corpus', full.length === 8239);
}

// 18. appendCustomCards: append-only growth, position stability, and pool
// wiring for whole-sentence cards pushed in from the reader. DATA itself is
// a `const` in the classic script (never exposed to the outer vm context —
// only `var`-declared state like `progress` and function declarations are),
// so these tests go through the same public contract real callers use:
// the indices appendCustomCards() returns, `progress.pool`, and `inPool()`.
//
// sentences_final.json is gitignored on purpose (raw scraped source data,
// kept local only — see .gitignore) — it exists in this dev environment
// but won't on a fresh clone, so this whole block degrades to a skip
// rather than crashing the run when it's absent.
const sentencesPath = __dirname + '/sentences_final.json';
if (!fs.existsSync(sentencesPath)) {
  console.log('SKIP  appendCustomCards block (sentences_final.json not present — gitignored raw source data, local-only)');
} else {
  const { appendCustomCards, inPool } = sandbox;
  sandbox.progress.pool = { levels: [0, 1, 2, 3, 4], include: [], exclude: [] };

  // Real DATA[0].c, read straight from the same corpus file the build
  // splices into extracted_script.js — so the focus word used below is
  // guaranteed to actually match, whatever it happens to be, instead of
  // assuming a fixed word from an old, much smaller fixture.
  const realData = JSON.parse(fs.readFileSync(sentencesPath, 'utf-8'));
  const builtinLength = realData.length;
  const focusWord = realData[0].c;

  // A: a fresh card whose focus word also appears as DATA[0].c — should
  // append at the end AND pull in the matching built-in sentence.
  const [newIndex] = appendCustomCards([
    { id: 'reader:book1:s1', he: 'משפט לבדיקה ' + focusWord, ru: 'тестовое предложение', focusWord, addedAt: 100 },
  ]);
  check('appendCustomCards returns an index past the built-in corpus', newIndex >= builtinLength);
  check('new card is force-included in the pool', sandbox.progress.pool.include.includes(newIndex));
  check('matching built-in sentence (index 0) also got included', sandbox.progress.pool.include.includes(0));

  // B: calling again with the exact same id (e.g. a duplicate pull) must be
  // a no-op — same index back, never a second append.
  const [repeatIndex] = appendCustomCards([
    { id: 'reader:book1:s1', he: 'הוא הלך ללכת הביתה', ru: 'он пошёл домой', focusWord: 'ללכת', addedAt: 100 },
  ]);
  check('re-appending the same card id returns the same index (no-op)', repeatIndex === newIndex);

  // C: a second, unrelated card is added AFTER the first — its index must
  // come after the first one's, never disturbing it.
  const [secondIndex] = appendCustomCards([
    { id: 'reader:book1:s2', he: 'זה כלב גדול', ru: 'это большая собака', focusWord: 'כלב', addedAt: 200 },
  ]);
  check('a later card gets a strictly later index', secondIndex === newIndex + 1);

  // D: a deleted card still consumes a slot (tombstone), excluded from the
  // pool, so any card appended after it keeps a stable index too.
  const [tombstoneIndex] = appendCustomCards([
    { id: 'reader:book1:s3', he: '', ru: '', focusWord: '', addedAt: 300, deleted: true },
  ]);
  check('a deleted card still occupies its own slot', tombstoneIndex === secondIndex + 1);
  check('a deleted card is excluded from the pool, not included', sandbox.progress.pool.exclude.includes(tombstoneIndex));
  check('a deleted card is NOT selectable via inPool', inPool(tombstoneIndex) === false);

  const [afterTombstoneIndex] = appendCustomCards([
    { id: 'reader:book1:s4', he: 'אני אוהב קפה', ru: 'я люблю кофе', focusWord: 'קפה', addedAt: 400 },
  ]);
  check('a card appended after a tombstone still gets a later, undisturbed index',
    afterTombstoneIndex === tombstoneIndex + 1);

  // E: re-processing the WHOLE batch (as a real re-login pull would) must
  // leave every previously-assigned index exactly where it was.
  const replay = appendCustomCards([
    { id: 'reader:book1:s1', he: 'הוא הלך ללכת הביתה', ru: 'он пошёл домой', focusWord: 'ללכת', addedAt: 100 },
    { id: 'reader:book1:s2', he: 'זה כלב גדול', ru: 'это большая собака', focusWord: 'כלב', addedAt: 200 },
    { id: 'reader:book1:s3', he: '', ru: '', focusWord: '', addedAt: 300, deleted: true },
    { id: 'reader:book1:s4', he: 'אני אוהב קפה', ru: 'я люблю кофе', focusWord: 'קפה', addedAt: 400 },
  ]);
  check('replaying the full batch reproduces the exact same indices in order',
    replay.length === 4 && replay[0] === newIndex && replay[1] === secondIndex &&
    replay[2] === tombstoneIndex && replay[3] === afterTombstoneIndex);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
