// Reference photos for change tasks, kept only in this browser (IndexedDB), never on the
// server: the backend keeps a text description and tells the page when to replace a photo.
// Storage can be blocked (private windows); then references live in memory for the visit.
// Next to a watched task's photo goes the tiny grey copy of its region the page compares
// with, so watching picks up where it left off after a reload or a camera break.
const NAME = 'memo-vision', STORE = 'references';
const greyKey = jobId => `${jobId}:grey`;
const memory = new Map();
let opening = null;

function open() {
  opening ||= new Promise((resolve, reject) => {
    const request = indexedDB.open(NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return opening;
}
async function run(mode, action) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const request = action(db.transaction(STORE, mode).objectStore(STORE));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export const references = {
  async get(jobId) {
    try { return (await run('readonly', store => store.get(jobId))) ?? memory.get(jobId) ?? null; }
    catch { return memory.get(jobId) ?? null; }
  },
  async put(jobId, image) {
    memory.set(jobId, image);
    try { await run('readwrite', store => store.put(image, jobId)); } catch { /* kept in memory */ }
  },
  // { grey, roi }: the region's grey copy and which region (JSON of the roi) it was taken of.
  async grey(jobId) {
    try { return (await run('readonly', store => store.get(greyKey(jobId)))) ?? memory.get(greyKey(jobId)) ?? null; }
    catch { return memory.get(greyKey(jobId)) ?? null; }
  },
  async putGrey(jobId, grey, roi) {
    const value = { grey, roi: JSON.stringify(roi ?? null) };
    memory.set(greyKey(jobId), value);
    try { await run('readwrite', store => store.put(value, greyKey(jobId))); } catch { /* kept in memory */ }
  },
  async remove(jobId) {
    memory.delete(jobId); memory.delete(greyKey(jobId));
    try {
      await run('readwrite', store => store.delete(jobId));
      await run('readwrite', store => store.delete(greyKey(jobId)));
    } catch { /* nothing stored */ }
  },
  async clear() {
    memory.clear();
    try { await run('readwrite', store => store.clear()); } catch { /* nothing stored */ }
  },
};
