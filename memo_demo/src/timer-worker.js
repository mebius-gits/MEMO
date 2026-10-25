// Timers for look tasks. A hidden tab's own timers are slowed to about once a minute; a
// worker's are not, so photo requests are still picked up while the person works elsewhere.
const timers = new Map();
self.onmessage = ({ data }) => {
  if (data.type === 'clear') { clearTimeout(timers.get(data.id)); clearInterval(timers.get(data.id)); timers.delete(data.id); return; }
  const fire = () => self.postMessage({ id: data.id });
  timers.set(data.id, data.type === 'every' ? setInterval(fire, data.ms) : setTimeout(() => { timers.delete(data.id); fire(); }, data.ms));
};
