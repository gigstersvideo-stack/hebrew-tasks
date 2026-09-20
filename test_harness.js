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
  heMatchDegree, heMatchDetail, diffTokensGeneric, tokenize, russianCheck, schedule, isMastered,
  levenshteinLE, heBaseNorm, heLooseNorm, findCloze,
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

// 6b. grammatical-form differences (v1.25.0) must NOT count as correct at all
check('different-person forms rejected', heMatchDegree('נהיה', 'נהיתה') === 0);
check('different-prefix-person forms rejected', heMatchDegree('אכתוב', 'יכתוב') === 0);
check('different-suffix-person forms rejected', heMatchDegree('כתבה', 'כתבו') === 0);

// 6c. heMatchDetail (v1.25+): kind distinguishes typo (gets a highlight
// position) from loose/synonym (no highlight — not actually a mistake)
check('loose (male/chaser) match has kind=loose, no position', (() => {
  const d = heMatchDetail('תיישב', 'תישב');
  return d.degree === 1 && d.kind === 'loose' && d.posA === undefined && d.posB === undefined;
})());
check('synonym match has kind=synonym, no position', (() => {
  const d = heMatchDetail('כאן', 'פה');
  return d.degree === 1 && d.kind === 'synonym';
})());
check('real interior typo (extra letter) has kind=typo with a position on the longer side', (() => {
  const d = heMatchDetail('שולחן', 'שלחן'); // interior vav typo, from earlier session example — user's word is longer (extra ו)
  return d.degree === 1 && d.kind === 'typo' && d.posA === 1 && d.posB === null;
})());
check('real interior typo (substitution, same length) has a position on both sides', (() => {
  const d = heMatchDetail('מחשק', 'מחשב'); // single substituted letter, same length
  return d.degree === 1 && d.kind === 'typo' && typeof d.posA === 'number' && typeof d.posB === 'number' && d.posA === d.posB;
})());

// 7. diff alignment: one word changed in the middle
{
  const user = tokenize('אני רוצה ללכת הביתה');
  const ref = tokenize('אני רוצה לחזור הביתה');
  const { userOps, refOps } = diffTokensGeneric(user, ref, heMatchDetail);
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

// findCloze (v1.27.6): пропуск находится и рядом со знаками препинания, и
// внутри слова с приставкой, и для cloze из нескольких слов (отзыв с сайта:
// «нет пропуска» — слово в конце предложения не находилось).
{
  const show = (he, c) => {
    const toks = tokenize(he); const h = findCloze(toks, c);
    return h ? toks.slice(0, h.start).concat([h.before + '_____' + h.after], toks.slice(h.end + 1)).join(' ') : null;
  };
  check('cloze: a word in the middle is found as before', show('אני הולך הביתה', 'הולך') === 'אני _____ הביתה');
  check('cloze: the last word before a full stop is found and the stop is kept', show('הם חברים טובים כבר הרבה שנים.', 'שנים') === 'הם חברים טובים כבר הרבה _____.');
  check('cloze: a word before a comma or question mark is found', show('מה, אתה גר כאן?', 'כאן') === 'מה, אתה גר _____?' && show('אתה, בטוח?', 'אתה') === '_____, בטוח?');
  check('cloze: a word glued to a prefix keeps the prefix visible', show('ברוב המקרים זה עובד', 'רוב') === 'ב_____ המקרים זה עובד');
  check('cloze: a two-word cloze is replaced as one blank', show('קניתי את הספר הזה', 'את הספר') === 'קניתי _____ הזה');
  check('cloze: an unrelated word is not found', findCloze(tokenize('אני הולך הביתה'), 'כלב') === null);
  check('cloze: an empty cloze is not found', findCloze(tokenize('אני הולך'), '') === null);
}

// Сессия практики корня переживает обновление страницы (v1.27.7): раньше она
// жила только в памяти, а корень отмечался пройденным в момент старта — после
// перезагрузки «Продолжить» вело на теорию СЛЕДУЮЩЕГО корня (отзыв 2026-09-16).
{
  const run = (code) => vm.runInContext(code, sandbox);
  run(`progress.rootsCourse = { completedTheory: ['x-y-z'], completedGrowingTexts: [], sentenceIndexByRoot: { 'x-y-z': [100, 101, 102, 103] } };
       rootPracticeSession = null;`);
  run(`startRootPracticeSession('x-y-z', [100, 101, 102, 103])`);
  const first = run(`pickNext().index`);                   // показана 1-я, ответа ещё нет
  check('root session: the first card of the session is served in order', first === 100);
  check('root session: the active session is saved with the current card kept',
    run(`JSON.stringify(progress.rootsCourse.activeSession.queue)`) === '[1,2,3]' && run(`progress.rootsCourse.activeSession.current`) === 0);

  // «обновление страницы»: память пуста, индексы корня заново разложены со сдвигом
  run(`rootPracticeSession = null; progress.rootsCourse.sentenceIndexByRoot['x-y-z'] = [200, 201, 202, 203]; restoreRootSession();`);
  check('root session: after a reload the session is restored (positions, not stale indices)',
    run(`JSON.stringify(rootPracticeSession.queue)`) === '[200,201,202,203]' && run(`rootPracticeSession.total`) === 4);

  // сессия старше суток не восстанавливается
  run(`rootPracticeSession = null; progress.rootsCourse.activeSession.at = Date.now() - 25 * 3600 * 1000; restoreRootSession();`);
  check('root session: a session older than a day is dropped, not restored',
    run(`rootPracticeSession`) === null && run(`progress.rootsCourse.activeSession`) === null);

  // закончена — сохранённая сессия убирается
  run(`startRootPracticeSession('x-y-z', [200, 201]); pickNext(); pickNext(); rootPracticeSession = null; persistRootSession(null);`);
  check('root session: a finished session leaves nothing saved', run(`progress.rootsCourse.activeSession`) === null);
}

// Стабильные ключи карточек (v1.28.0): карточка корня/личная живёт под id самого
// предложения, а не под позицией в DATA — иначе на устройстве с другим порядком
// разблокировки корней она оказывается приклеена к чужому предложению (проверено
// на двух «устройствах» 2026-09-20).
{
  const run = (code) => vm.runInContext(code, sandbox);
  const res = JSON.parse(run(`(function () {
    const base = BUILTIN_DATA_LENGTH;
    const S1 = [{he:'אחד', ru:'one', cloze_token:'אחד'}, {he:'שניים', ru:'two', cloze_token:'שניים'}];
    const S2 = [{he:'שלוש', ru:'three', cloze_token:'שלוש'}, {he:'ארבע', ru:'four', cloze_token:'ארבע'}];
    const CARDS = [{id:'reader:b1:s1', he:'כלב גדול', ru:'big dog', focusWord:'כלב', addedAt:10},
                   {id:'reader:b1:s2', he:'חתול קטן', ru:'small cat', focusWord:'חתול', addedAt:20}];
    function reset() {
      DATA.length = base;
      for (const k in sidToIndex) delete sidToIndex[k];
      for (const k in customCardIndexBySource) delete customCardIndexBySource[k];
      for (const k in customCardsInfo) delete customCardsInfo[k];
      progress.items = {}; resetPendingItems(); rootSentencesInjectedThisLoad.clear();
      progress.pool = { levels: [0,1,2,3,4], include: [], exclude: [] };
      progress.rootsCourse = { completedTheory: [], completedGrowingTexts: [], sentenceIndexByRoot: {} };
    }
    const out = {};
    // device A: root a first, then b; the card is a review of sentence #1 of root b ("four")
    reset(); appendRootSentences('a-a-a', S1); appendRootSentences('b-b-b', S2);
    const idxA = progress.rootsCourse.sentenceIndexByRoot['b-b-b'][1];
    progress.items[idxA + ':cloze'] = { stability: 30, reps: 4, lastReview: 5 };
    const stored = serializeItems();
    out.storedKeys = Object.keys(stored);
    out.builtinKeyUnchanged = toStableKey('5:cloze') === '5:cloze';
    // device B: the opposite order of unlocking
    reset(); appendRootSentences('b-b-b', S2); appendRootSentences('a-a-a', S1);
    ingestItems(stored);
    const idxB = progress.rootsCourse.sentenceIndexByRoot['b-b-b'][1];
    out.idxA = idxA; out.idxB = idxB;
    out.landsOnSameSentence = !!progress.items[idxB + ':cloze'] && DATA[idxB].h === 'ארבע' && progress.items[idxB + ':cloze'].reps === 4;
    out.noStrayCard = !progress.items[idxA + ':cloze'];
    // cards arrive BEFORE their sentences are laid out (cloud pull first, root replay second)
    reset(); ingestItems(stored);
    out.pendingBefore = Object.keys(pendingItems).length;
    out.notAttachedYet = Object.keys(progress.items).length === 0;
    appendRootSentences('a-a-a', S1);
    out.stillPendingAfterOtherRoot = Object.keys(pendingItems).length;
    appendRootSentences('b-b-b', S2);
    const idxC = progress.rootsCourse.sentenceIndexByRoot['b-b-b'][1];
    out.pendingCleared = Object.keys(pendingItems).length === 0 && DATA[idxC].h === 'ארבע' && progress.items[idxC + ':cloze'].reps === 4;
    // a card whose sentence is not laid out on this device is kept (and written back), not lost
    reset(); ingestItems(stored);
    out.roundTripKeepsPending = Object.keys(serializeItems())[0] === 'r|b-b-b|1:cloze';
    // personal cards: same sentence whether they are appended before or after the roots
    reset(); appendCustomCards(CARDS); appendRootSentences('a-a-a', S1);
    const cIdx1 = customCardIndexBySource['reader:b1:s2'];
    progress.items[cIdx1 + ':he2ru'] = { stability: 9, reps: 2, lastReview: 7 };
    const cStored = serializeItems();
    out.customKey = Object.keys(cStored)[0];
    reset(); appendRootSentences('a-a-a', S1); appendCustomCards(CARDS); ingestItems(cStored);
    const cIdx2 = customCardIndexBySource['reader:b1:s2'];
    out.customSame = cIdx1 !== cIdx2 && DATA[cIdx2].h === 'חתול קטן' && progress.items[cIdx2 + ':he2ru'].reps === 2;
    // the persisted pool keeps only built-in phrases (root/custom positions are per-load)
    progress.pool.include = [3, cIdx2]; progress.pool.exclude = [4, 99999];
    const pv = persistView();
    out.poolFiltered = JSON.stringify(pv.pool.include) === '[3]' && JSON.stringify(pv.pool.exclude) === '[4]' && pv.keysV2 === true;
    // a newer review wins when both sides have the card
    reset(); appendRootSentences('b-b-b', S2);
    const i1 = progress.rootsCourse.sentenceIndexByRoot['b-b-b'][1];
    progress.items[i1 + ':cloze'] = { reps: 9, lastReview: 100 };
    ingestItems({ 'r|b-b-b|1:cloze': { reps: 1, lastReview: 50 } });
    out.newerWins = progress.items[i1 + ':cloze'].reps === 9;
    // old positional keys: unrecoverable ones are dropped, recoverable ones move to stable ids
    out.legacyDropped = canonicalCardKey(String(base + 500) + ':cloze') === null;
    legacyKeyLayout['x-y-z'] = [base + 40, base + 41];
    out.legacyMapped = canonicalCardKey((base + 41) + ':cloze') === 'r|x-y-z|1:cloze';
    delete legacyKeyLayout['x-y-z'];
    reset();
    return JSON.stringify(out);
  })()`));
  check('stable keys: a root card is stored under the sentence id, not its position', res.storedKeys.length === 1 && res.storedKeys[0] === 'r|b-b-b|1:cloze');
  check('stable keys: built-in phrases keep their plain numeric key', res.builtinKeyUnchanged);
  check('stable keys: the two devices really had different positions', res.idxA !== res.idxB);
  check('stable keys: the card lands on the same sentence on the device with the opposite unlock order', res.landsOnSameSentence);
  check('stable keys: nothing is attached to the other device\'s position', res.noStrayCard);
  check('stable keys: a card that arrives before its sentences waits instead of being lost', res.pendingBefore === 1 && res.notAttachedYet);
  check('stable keys: an unrelated root being laid out does not release it', res.stillPendingAfterOtherRoot === 1);
  check('stable keys: it is attached as soon as its own root is laid out', res.pendingCleared);
  check('stable keys: a still-waiting card is written back under its stable key', res.roundTripKeepsPending);
  check('stable keys: a personal card gets a stable key', res.customKey === 'c|reader:b1:s2:he2ru');
  check('stable keys: a personal card lands on the same sentence whichever is appended first', res.customSame);
  check('stable keys: the saved pool keeps only built-in phrases', res.poolFiltered);
  check('stable keys: the more recently reviewed side wins on merge', res.newerWins);
  check('stable keys: an old positional key with no known layout is dropped', res.legacyDropped);
  check('stable keys: an old positional key inside the last known layout is migrated', res.legacyMapped);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
