/* EF Studios Artist Finder — client behaviour: drawer, toasts, jobs, live search. */
const App = (() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  let dirty = false;

  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    $('#toasts').appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; }, 2200);
    setTimeout(() => t.remove(), 2600);
  }

  async function updateLead(id, data, msg) {
    const r = await fetch('/api/lead/' + id, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    dirty = true;
    if (msg && r.ok) toast(msg);
    return r.ok;
  }

  async function copy(btn, text) {
    try { await navigator.clipboard.writeText(text); } catch (e) {
      const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t);
      t.select(); document.execCommand('copy'); t.remove();
    }
    toast('Copied ' + text);
  }

  /* ---------------- drawer ---------------- */
  const drawer = () => $('#drawer');
  async function openDrawer(id) {
    const d = drawer();
    d.innerHTML = '<div style="padding:40px;display:flex;justify-content:center"><span class="spin"></span></div>';
    d.classList.add('on'); d.setAttribute('aria-hidden', 'false');
    $('#scrim').classList.add('on');
    d.dataset.id = id;
    const r = await fetch('/lead/' + id + '/drawer');
    d.innerHTML = r.ok ? await r.text() : '<p style="padding:30px">Couldn\'t load this artist.</p>';
    wireDrawer(d);
  }
  function closeDrawer() {
    const d = drawer();
    if (!d.classList.contains('on')) return;
    d.classList.remove('on'); d.setAttribute('aria-hidden', 'true');
    $('#scrim').classList.remove('on');
    if (dirty && document.body.dataset.refreshOnClose === '1') location.reload();
  }
  function wireDrawer(root) {
    const id = root.dataset.id || root.querySelector('[data-lead]')?.dataset.lead;
    root.querySelectorAll('.seg [data-status]').forEach(b => b.addEventListener('click', async () => {
      root.querySelectorAll('.seg [data-status]').forEach(x => x.classList.toggle('on', x === b));
      await updateLead(id, {status: b.dataset.status}, 'Moved to ' + b.textContent.trim());
    }));
    const notes = root.querySelector('textarea[data-notes]');
    if (notes) notes.addEventListener('change', () => updateLead(id, {notes: notes.value}, 'Notes saved'));
    const genres = root.querySelector('input[data-genres]');
    if (genres) genres.addEventListener('change', () => updateLead(id, {genres: genres.value}, 'Genres saved'));
    root.querySelectorAll('[data-recheck]').forEach(b => b.addEventListener('click', async () => {
      await fetch('/lead/' + id + '/recheck', {method: 'POST', headers: {'X-Requested-With': 'fetch'}});
      toast('Re-checking in the background');
      pollJobs();
    }));
    root.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', closeDrawer));
  }

  document.addEventListener('click', e => {
    const tile = e.target.closest('[data-id].tile, [data-id].mini, [data-open]');
    if (tile && !e.target.closest('a, button, input, select, textarea')) {
      if (tile.classList.contains('mini') && tile.dataset.dragged) { delete tile.dataset.dragged; return; }
      openDrawer(tile.dataset.id || tile.dataset.open);
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDrawer();
    if (e.key === 'Enter' && e.target.classList?.contains('tile')) openDrawer(e.target.dataset.id);
  });
  document.addEventListener('DOMContentLoaded', () => {
    $('#scrim')?.addEventListener('click', closeDrawer);
    document.querySelectorAll('.page-drawer').forEach(wireDrawer);
  });

  /* ---------------- background jobs ---------------- */
  const running = new Set();
  let pollTimer = null;
  async function pollJobs() {
    clearTimeout(pollTimer);
    let jobs = [];
    try { jobs = await (await fetch('/api/jobs')).json(); } catch (e) { pollTimer = setTimeout(pollJobs, 5000); return; }
    const active = jobs.filter(j => j.status === 'running' || j.status === 'queued');
    for (const j of jobs) {
      if (running.has(j.id) && !(j.status === 'running' || j.status === 'queued')) {
        running.delete(j.id);
        if (j.status === 'done' && j.message) toast(j.message.charAt(0).toUpperCase() + j.message.slice(1));
        if (j.status === 'failed') toast('Something went wrong: ' + j.message);
        if (window.onJobDone) window.onJobDone(j);
      }
    }
    active.forEach(j => running.add(j.id));
    const box = $('#jobbox');
    if (box) {
      box.classList.toggle('on', active.length > 0);
      const j = active.find(j => j.status === 'running') || active[0];
      if (j) $('#jobtext').textContent = (j.message || j.kind) + (active.length > 1 ? ` (+${active.length - 1} queued)` : '');
    }
    pollTimer = setTimeout(pollJobs, active.length ? 2000 : 6000);
  }
  document.addEventListener('DOMContentLoaded', pollJobs);

  /* ---------------- live search results ---------------- */
  function watchSearch(id) {
    const grid = $('#results'), status = $('#search-status');
    async function tick() {
      let d;
      try { d = await (await fetch('/api/search/' + id)).json(); } catch (e) { return setTimeout(tick, 4000); }
      if (grid && d.html.trim()) grid.innerHTML = d.html;
      const done = !(d.status === 'running' || d.status === 'queued');
      if (status) status.innerHTML = done
        ? `<span class="badge conf-high">${d.count} found</span> <span class="muted small">${esc(d.message)}</span>`
        : `<span class="spin"></span> <span class="muted small">${esc(d.message || 'Starting…')}</span>`;
      if (done && !d.count && grid) grid.innerHTML = '<div class="empty" style="grid-column:1/-1"><div class="big">🔎</div>No matches this time. Try a broader prompt, or fewer requirements.</div>';
      if (!done) setTimeout(tick, 2500);
    }
    tick();
  }

  return {toast, updateLead, copy, openDrawer, closeDrawer, watchSearch, pollJobs, esc};
})();
