// test_cloud_sync.js — запускает НАСТОЯЩИЙ текст pullAndMerge/commitInBatches из index.html на поддельном Firestore
// (v1.28.0, стабильные ключи карточек): старые числовые документы мигрируют, побеждает более свежая оценка,
// корни в обратном порядке раскладки находят свои карточки. Запуск: node test_cloud_sync.js
// Runs the REAL pullAndMerge / commitInBatches text from index.html against a fake Firestore.
const vm = require('vm'), fs = require('fs');
const html = fs.readFileSync(__dirname + '/index.html', 'utf-8');
const classic = /<script>([\s\S]*?)<\/script>/.exec(html)[1];
const mod = /<script type="module">([\s\S]*?)<\/script>/.exec(html)[1];
const grab = (name) => {
  const i = mod.search(new RegExp('(async )?function ' + name + '\\('));
  let depth = 0, j = mod.indexOf('{', i);
  for (let k = j; k < mod.length; k++) { if (mod[k] === '{') depth++; if (mod[k] === '}') { depth--; if (depth === 0) return mod.slice(i, k + 1); } }
};
const store = {};
function fakeEl() { return { style: new Proxy({}, { get: () => '', set: () => true }), classList: { add() {}, remove() {}, toggle() {}, contains: () => false }, dataset: {}, addEventListener() {}, appendChild() {}, querySelectorAll: () => [], setAttribute() {}, removeAttribute() {}, focus() {}, value: '', textContent: '', innerHTML: '', children: [], childElementCount: 0 }; }
const els = {};
const sandbox = {
  document: { getElementById: (id) => els[id] || (els[id] = fakeEl()), createElement: () => fakeEl(), createTextNode: (t) => ({ textContent: t }), querySelectorAll: () => [], addEventListener() {} },
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
  fetch: () => new Promise(() => {}), window: {}, console, Date, Math, Array, Object, JSON, Set, Number, String, Boolean, RegExp, Promise, setTimeout,
};
sandbox.window.speechSynthesis = undefined; sandbox.navigator = { onLine: true, maxTouchPoints: 0 }; sandbox.window.addEventListener = () => {};
// device with an OLD-format local state (numeric keys, last known layout of two roots)
const st = (r, t) => ({ difficulty: 5, stability: r, due: 1, lastReview: t, phase: 'review', stepIndex: 0, reps: r, lapses: 0, seen: r, correct: r });
store['ivrit_trainer_progress_v3'] = JSON.stringify({
  items: { '8241:cloze': st(3, 100), '5:ru2he': st(4, 100) },
  meta: { dailyNewCap: 20, newToday: { date: '2000-01-01', count: 0 }, autoPlayAudio: true }, mistakesHe: {}, mistakesRu: {}, streak: { lastDate: null, count: 0 },
  pool: { levels: [0, 1, 2, 3, 4, 5], include: [8300], exclude: [8250] },
  rootsCourse: { completedTheory: ['ה-י-ה', 'ש-נ-ה'], completedGrowingTexts: [], sentenceIndexByRoot: { 'ה-י-ה': Array.from({ length: 20 }, (_, k) => 8239 + k), 'ש-נ-ה': Array.from({ length: 20 }, (_, k) => 8259 + k) } },
});
vm.createContext(sandbox);
vm.runInContext(classic, sandbox);

// fake cloud: OLD docs written by an old-version device (numeric), one newer than local, one only in the cloud, one unmappable
const cloud = {
  '8241:cloze': st(9, 200),      // root ה-י-ה #2, reviewed later than local -> must win
  '8260:cloze': st(2, 150),      // root ש-נ-ה #1, only in the cloud
  '9999:cloze': st(7, 150),      // outside any known layout
  '5:ru2he': st(1, 50),          // built-in, older than local
};
const writes = {}, deletes = [];
const fake = `
  const fbDb = {}; const cardsCol = () => 'cards'; const metaDocRef = () => 'meta';
  const getDocs = async () => ({ forEach: (f) => Object.keys(cloud).forEach(id => f({ id, data: () => cloud[id] })) });
  const getDoc = async () => ({ exists: () => true, data: () => ({ pool: { levels: [0,1,2,3,4,5], include: [8300, 3], exclude: [8250, 7] }, rootsCourse: { completedTheory: ['ה-י-ה','ש-נ-ה'], completedGrowingTexts: [] }, meta: {} }) });
  const setDoc = async () => {};
  const withAuthRaceRetry = (fn) => fn();
  const writeBatch = () => ({ set: (ref, v) => {}, commit: async () => {} });
  const doc = (col, id) => id;
  window.cloudDeleteCards = async (keys) => { deletes.push(...keys); };
  async function loadRootsCourseData() { return { sentencesByRoot: {} }; }
  function metaPayload() { return { pool: persistedPool() }; }
`;
sandbox.cloud = cloud; sandbox.writes = writes; sandbox.deletes = deletes;
const code = fake + '\n' + grab('commitInBatches') + '\n' + grab('mergeCardStates') + '\n' + grab('mergeCounterMaps') + '\n' + grab('pullAndMerge') +
  '\nasync function run() { await pullAndMerge("u1"); return { items: Object.keys(progress.items), pending: Object.keys(pendingItems), pool: progress.pool }; }';
// commitInBatches -> capture what would be written under which keys
const patched = code.replace(/batch\.set\(doc\(cardsCol\(uid\), k\), valueFor\(k\)\);/, 'writes[k] = valueFor(k); batch.set(0,0);');
vm.runInContext(patched, sandbox);
(async () => {
  const out = await vm.runInContext('run()', sandbox);
  // now lay the roots out and see where the cards land
  const S = (n) => Array.from({ length: 20 }, (_, k) => ({ he: 'מ' + n + '_' + k, ru: 'r', cloze_token: 'x' }));
  vm.runInContext(`appendRootSentences('ש-נ-ה', ${JSON.stringify(S(2))}); appendRootSentences('ה-י-ה', ${JSON.stringify(S(1))});`, sandbox);   // opposite order on purpose
  const land = JSON.parse(vm.runInContext(`JSON.stringify({ r1s2: progress.items[progress.rootsCourse.sentenceIndexByRoot['ה-י-ה'][2] + ':cloze'], r2s1: progress.items[progress.rootsCourse.sentenceIndexByRoot['ש-נ-ה'][1] + ':cloze'], builtin: progress.items['5:ru2he'], stray: Object.keys(progress.items).filter(k => /^(9999|8241|8260):/.test(k)) })`, sandbox));
  console.log('written under keys:', Object.keys(writes).sort());
  console.log('deleted legacy docs:', deletes.sort());
  console.log('r1 #2 (newer cloud wins) reps:', land.r1s2 && land.r1s2.reps, ' r2 #1 (cloud-only) reps:', land.r2s1 && land.r2s1.reps, ' builtin (local newer) reps:', land.builtin && land.builtin.reps, ' stray:', land.stray);
  console.log('pool:', JSON.stringify(vm.runInContext('progress.pool', sandbox)));
  const pool = vm.runInContext('progress.pool', sandbox);
  const checks = [
    ['cards are written under stable keys', JSON.stringify(Object.keys(writes).sort()) === JSON.stringify(['5:ru2he', 'r|ה-י-ה|2:cloze', 'r|ש-נ-ה|1:cloze'].sort())],
    ['old positional cloud docs are removed', JSON.stringify(deletes) === JSON.stringify(['8241:cloze', '8260:cloze', '9999:cloze'].sort())],
    ['the more recent review wins (cloud)', land.r1s2 && land.r1s2.reps === 9],
    ['a cloud-only card lands on its own sentence', land.r2s1 && land.r2s1.reps === 2],
    ['the more recent review wins (local, built-in)', land.builtin && land.builtin.reps === 4],
    ['nothing is left on a stray position', land.stray.length === 0],
    ['pool keeps only built-in phrases', JSON.stringify(pool.include) === '[3]' && JSON.stringify(pool.exclude) === '[7]'],
  ];
  let bad = 0;
  for (const [name, ok] of checks) { console.log(ok ? 'OK   ' : 'FAIL ', name); if (!ok) bad++; }
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error('ERR', e); process.exit(1); });
