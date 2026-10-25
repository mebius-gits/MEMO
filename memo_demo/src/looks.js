// The page is MEMO's camera. While the camera is on, MEMO observes the space on its own:
// every 2 s a tiny grey copy of the frame is compared with the last settled scene, for the
// whole space and for each watched task's region (nothing leaves the page). When the space
// changes and settles, someone appears or leaves, or a patrol is due, a photo goes to the
// backend (/observe). When a watched region changes and settles, that task runs at once with
// a photo of the moment the change began. Look tasks that are simply due are polled for every
// few seconds; a new situation there needs a confirming photo, which is taken a few seconds
// after the first in any case, so it can be sent the moment it is asked for. All of this also
// runs while the tab is hidden, on worker timers.
import { createChangeWatch } from './perception.js';
import { references } from './references.js';

const POLL_MS = 5000, CONFIRM_MS = 6000, WATCH_MS = 2000;
// A face seen this long means someone arrived; unseen this long, that they left.
const ARRIVE_MS = 3000, LEAVE_MS = 30000;

// Timers that keep time in a hidden tab (see timer-worker.js); plain timers as a fallback.
export function createClock() {
  const handlers = new Map();
  let worker = null, next = 1;
  try {
    worker = new Worker(new URL('./timer-worker.js', import.meta.url), { type: 'module' });
    worker.onmessage = ({ data }) => handlers.get(data.id)?.();
  } catch { worker = null; }
  return {
    every(ms, callback) {
      const id = next++;
      if (!worker) { const timer = setInterval(callback, ms); return () => clearInterval(timer); }
      handlers.set(id, callback); worker.postMessage({ type: 'every', id, ms });
      return () => { handlers.delete(id); worker.postMessage({ type: 'clear', id }); };
    },
    after(ms) {
      return new Promise(resolve => {
        if (!worker) { setTimeout(resolve, ms); return; }
        const id = next++;
        handlers.set(id, () => { handlers.delete(id); resolve(); });
        worker.postMessage({ type: 'after', id, ms });
      });
    },
  };
}

export function createLookRunner({ camera, request, device, onNotice = () => {}, onJobs = () => {}, onPhoto = () => {}, onObserved = () => {} }) {
  const clock = createClock(), busy = new Set();
  // Several open tabs share one camera: only the tab holding the lock takes photos.
  let leader = !navigator.locks, activeJobs = 0, stops = [];
  function lead() {
    navigator.locks?.request('memo-look-camera', { ifAvailable: true }, lock => {
      if (!lock) return undefined;
      leader = true;
      return new Promise(() => {}); // held for the life of the page
    });
  }
  const path = suffix => `/api/v1/devices/${encodeURIComponent(device())}${suffix}`;
  const post = (suffix, body) => request(path(suffix), { method: 'POST', body: JSON.stringify(body) });
  // Why there is no photo; the backend records it, so the history shows the gap.
  const skipReason = () => ({ denied: 'permission_denied', off: 'camera_off' })[camera.state] || 'camera_unavailable';
  const shoot = (roi, maxSide) => camera.active ? camera.photo({ roi, maxSide }).catch(() => null) : Promise.resolve(null);
  const photoBody = (task, attempt, shot, extra = {}) => ({
    task_id: task.task_id, attempt, image: shot.image, quality: shot.quality, captured_at: shot.takenAt.toISOString(), ...extra,
  });

  // ---- Look tasks ----
  async function run(task, { moment = null } = {}) {
    const shot = await shoot(task.roi);
    if (!shot) return post('/captures', { task_id: task.task_id, attempt: task.attempt, skip: skipReason() });
    onPhoto(task);
    const watched = Boolean(moment);
    const second = task.attempt === 1 && !watched ? clock.after(CONFIRM_MS).then(() => shoot(task.roi)) : null;
    // A change task compares with its reference photo; a baseline run makes a new one.
    const reference = task.mode === 'change' && !task.baseline ? await references.get(task.job_id) : null;
    const extra = { ...(reference ? { reference } : {}), ...(watched ? { trigger: 'watch', moment } : {}) };
    let result = await post('/captures', photoBody(task, task.attempt, shot, extra));
    let kept = shot;
    if (result.decision === 'confirm' && second) {
      const confirming = await second;
      result = await post('/captures', confirming ? photoBody(task, 2, confirming, extra) : { task_id: task.task_id, attempt: 2, skip: skipReason() });
      kept = confirming || shot;
    }
    if (result.store_reference) await references.put(task.job_id, kept.image);
    // What was just photographed is the region's settled scene from now on.
    if (task.mode === 'change') regions.get(task.job_id)?.rebase(await camera.sample({ roi: task.roi, width: 48 }).catch(() => null));
    if (result.status === 'queued') onNotice();
    return result;
  }
  function start(task, options) {
    if (busy.has(task.task_id)) return;
    busy.add(task.task_id);
    run(task, options)
      // 409: another tab took this photo, or it is no longer wanted.
      .catch(error => { if (error.status !== 409) console.warn('look task', error.message); })
      .finally(() => busy.delete(task.task_id));
  }
  let watching = [];
  async function poll() {
    if (!leader) lead();
    if (!leader) return;
    let due;
    try { due = await request(path('/captures/pending')); } catch { return; }
    if (due.active_jobs !== activeJobs) { activeJobs = due.active_jobs; onJobs(activeJobs); }
    watching = due.watching || [];
    for (const task of due.requests) start(task);
  }

  // ---- MEMO's own observation ----
  const space = createChangeWatch({ threshold: 10 });
  const regions = new Map(), moments = new Map();
  let lastLook = 0, observing = false, patrolMs = 15 * 60000, gapMs = 20000;
  const presence = { here: null, seenSince: 0, lastSeen: 0 };
  async function observe(trigger) {
    if (observing || !camera.active) return;
    observing = true;
    try {
      const shot = await shoot(null, 1024);
      if (!shot) return;
      lastLook = Date.now();
      onPhoto({ trigger });
      const result = await post('/observe', { trigger, image: shot.image, quality: shot.quality, captured_at: shot.takenAt.toISOString() });
      space.rebase(await camera.sample().catch(() => null));
      onObserved(result);
      if (result.notice) onNotice();
    } catch (error) { if (error.status !== 409) console.warn('observe', error.message); }
    finally { observing = false; }
  }
  // The face tracker reports every reading; a face that stays means someone arrived, a face
  // gone for a while that they left. MEMO then looks to see who and what is there.
  function face(present, now = Date.now()) {
    if (present) { presence.lastSeen = now; presence.seenSince ||= now; } else presence.seenSince = 0;
    if (presence.here !== true && present && now - presence.seenSince >= ARRIVE_MS) {
      const first = presence.here === null; presence.here = true;
      if (!first) observe('person_enter');
    } else if (presence.here !== false && !present && now - presence.lastSeen >= LEAVE_MS) {
      const first = presence.here === null; presence.here = false;
      if (!first) observe('person_leave');
    }
  }
  async function watch() {
    if (!leader || !camera.active) return;
    const now = Date.now();
    try {
      const frame = await camera.sample();
      if (!lastLook) { space.rebase(frame); observe('patrol'); return; }
      if (space.update(frame) === 'settled' && now - lastLook >= gapMs) observe('scene_change');
      else if (now - lastLook >= patrolMs) observe('patrol');
      for (const task of watching) {
        if (!regions.has(task.job_id)) regions.set(task.job_id, createChangeWatch());
        const region = regions.get(task.job_id), state = region.update(await camera.sample({ roi: task.roi, width: 48 }));
        // The moment something starts to happen in the region (often a hand reaching in).
        if (state === 'onset') shoot(task.roi, 640).then(shot => shot && moments.set(task.job_id, shot.image));
        if (state !== 'settled' || busy.size) continue;
        region.rebase(await camera.sample({ roi: task.roi, width: 48 }));
        const moment = moments.get(task.job_id); moments.delete(task.job_id);
        try {
          const triggered = await post(`/schedules/${task.job_id}/trigger`, {});
          start(triggered, { moment: moment || (await shoot(task.roi, 640))?.image });
        } catch (error) { if (error.status !== 409) console.warn('watch', error.message); }
      }
    } catch { /* the camera stopped between checks */ }
  }

  return {
    async start() {
      if (stops.length) return;
      try { const config = await request(path('/observation')); patrolMs = config.patrol_minutes * 60000; gapMs = config.min_gap_seconds * 1000; } catch { /* defaults */ }
      stops = [clock.every(POLL_MS, poll), clock.every(WATCH_MS, watch)];
      poll();
    },
    stop() { stops.forEach(stop => stop()); stops = []; },
    poll, face,
    get activeJobs() { return activeJobs; },
  };
}
