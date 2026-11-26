/* Solo Tracker — frontend (tanpa framework). */

const $ = (id) => document.getElementById(id);

const SCHEDULE = [
  { label: 'Tutup Pendaftaran Solo', date: 'Jumat, 4 Des 2026, 00.00 WIB', iso: '2026-12-04T00:00:00+07:00' },
  { label: 'Mulai Submit', date: 'Sabtu, 5 Des 2026, 00.00 WIB', iso: '2026-12-05T00:00:00+07:00' },
  { label: 'Tutup Submit', date: 'Kamis, 10 Des 2026, 00.00 WIB', iso: '2026-12-10T00:00:00+07:00' },
  { label: 'Pengumuman', date: 'Senin, 14 Des 2026, 00.00 WIB', iso: '2026-12-14T00:00:00+07:00' },
];

const BADGE = {
  'Siap Submit': 'Siap_Submit',
  'Menunggu Penilaian': 'Menunggu_Penilaian',
  'Valid': 'Valid',
  'Duplikat': 'Duplikat',
  'Ditolak': 'Ditolak',
};

let entries = [];
let foundSubdomains = [];
let aliveMap = {};

/* ---------- util ---------- */
async function api(url, options = {}) {
  const res = await fetch(url, options);
  let data;
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function toast(msg, isErr = false) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('err', isErr);
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 2600);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  }
}

function fmtWib(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('id-ID', {
    timeZone: 'Asia/Jakarta', day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }) + ' WIB';
}

/* ---------- countdown ---------- */
function renderCountdown() {
  const now = Date.now();
  $('countdown').innerHTML = SCHEDULE.map((s) => {
    const diff = new Date(s.iso).getTime() - now;
    let time = 'Selesai';
    let cls = 'is-done';
    if (diff > 0) {
      const d = Math.floor(diff / 86400000);
      const h = Math.floor((diff % 86400000) / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const sec = Math.floor((diff % 60000) / 1000);
      time = `${d}h ${String(h).padStart(2, '0')}j ${String(m).padStart(2, '0')}m ${String(sec).padStart(2, '0')}d`;
      cls = '';
    } else if (diff > -3600000) {
      time = 'Baru saja';
      cls = 'is-live';
    }
    return `<div class="cd-item ${cls}">
      <div class="cd-label">${esc(s.label)}</div>
      <div class="cd-date">${esc(s.date)}</div>
      <div class="cd-time">${esc(time)}</div>
    </div>`;
  }).join('');
}

/* ---------- stats ---------- */
async function loadStats() {
  const s = await api('/api/stats');
  $('stat-score').textContent = s.total_score ?? 0;
  $('stat-waiting').textContent = s['Menunggu Penilaian'] ?? 0;
  $('stat-total').textContent = s.total_entries ?? 0;
}

/* ---------- bulk cleaner ---------- */
async function processBulk() {
  const urls = $('bulk-input').value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return toast('Tempel URL terlebih dahulu.', true);

  const btn = $('process-btn');
  btn.disabled = true;
  btn.textContent = 'Memproses…';
  try {
    const r = await api('/api/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls }),
    });

    $('count-unique').textContent = r.unique_count;
    $('count-duplicate').textContent = r.duplicate_count;
    $('count-invalid').textContent = r.invalid_count;
    $('process-summary').textContent = `${r.total} entri diproses`;

    $('list-unique').innerHTML = r.uniques.length
      ? r.uniques.map((e) => `<div class="res-item">
          <div>${esc(e.submit_url)}</div>
          <div class="key">→ ${esc(e.canonical_key)}</div>
        </div>`).join('')
      : '<div class="muted">Tidak ada</div>';

    $('list-duplicate').innerHTML = r.duplicates.length
      ? r.duplicates.map((e) => `<div class="res-item">
          <div>${esc(e.raw)}</div>
          <div class="why">${esc(e.reason)}</div>
        </div>`).join('')
      : '<div class="muted">Tidak ada</div>';

    $('list-invalid').innerHTML = r.invalids.length
      ? r.invalids.map((e) => `<div class="res-item">
          <div>${esc(e.raw)}</div>
          <div class="why">${esc(e.reason)}</div>
        </div>`).join('')
      : '<div class="muted">Tidak ada</div>';

    toast(`${r.unique_count} unik masuk antrean`);
    await loadQueue();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Proses & Masuk Antrean';
  }
}

/* ---------- subdomain OSINT ---------- */
async function searchSubdomains() {
  const domain = $('domain-input').value.trim();
  if (!domain) return toast('Masukkan domain induk.', true);

  const btn = $('subdomain-btn');
  btn.disabled = true;
  $('subdomain-status').textContent = 'Menghubungi crt.sh…';
  foundSubdomains = [];
  $('add-subdomains-btn').disabled = true;

  try {
    const r = await api(`/api/subdomains?domain=${encodeURIComponent(domain)}`);
    foundSubdomains = r.subdomains || [];
    $('subdomain-list').innerHTML = foundSubdomains.length
      ? foundSubdomains.map((s) => `<span class="chip">${esc(s)}</span>`).join('')
      : '<span class="muted">Tidak ada subdomain ditemukan.</span>';
    $('subdomain-status').textContent = `${foundSubdomains.length} subdomain ditemukan`;
    $('add-subdomains-btn').disabled = foundSubdomains.length === 0;
  } catch (err) {
    $('subdomain-status').textContent = err.message;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function addSubdomains() {
  if (!foundSubdomains.length) return;
  const r = await api('/api/process', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls: foundSubdomains }),
  });
  toast(`${r.unique_count} subdomain masuk antrean`);
  foundSubdomains = [];
  document.querySelectorAll('#subdomain-list .chip').forEach((c) => c.classList.add('checked'));
  $('add-subdomains-btn').disabled = true;
  await loadQueue();
}

/* ---------- queue ---------- */
async function loadQueue() {
  const r = await api('/api/entries');
  entries = r.entries || [];
  renderQueue();
  await loadStats();
}

function renderQueue() {
  const filter = $('status-filter').value;
  const shown = filter === 'all' ? entries : entries.filter((e) => e.status === filter);

  if (!shown.length) {
    $('queue-list').innerHTML = '<div class="empty">Belum ada entri pada filter ini.</div>';
    return;
  }

  $('queue-list').innerHTML = shown.map((e) => {
    const st = e.status;
    const locked = ['Menunggu Penilaian', 'Valid', 'Duplikat'].includes(st);
    const badgeCls = BADGE[st] ? BADGE[st].replace(/\s+/g, '_') : 'Siap_Submit';
    const alive = aliveMap[e.canonical_key];
    const aliveHtml = alive ? `<span class="alive ${alive.alive ? 'yes' : 'no'}">${esc(alive.label)}</span>` : '';

    let actions = '';
    if (st === 'Siap Submit') {
      actions = `
        <button class="btn small ghost" onclick="checkAlive('${esc(e.canonical_key)}')">Cek Hidup</button>
        <button class="btn small primary" onclick="submitEntry('${esc(e.canonical_key)}')">Salin URL &amp; Tandai Submitted</button>
        <button class="btn small danger" onclick="setStatus('${esc(e.canonical_key)}','Duplikat')">Tandai Duplikat</button>`;
    } else if (st === 'Menunggu Penilaian') {
      actions = `
        <button class="btn small primary" onclick="setStatus('${esc(e.canonical_key)}','Valid')">Tandai Valid (+1)</button>
        <button class="btn small danger" onclick="setStatus('${esc(e.canonical_key)}','Duplikat')">Tandai Duplikat</button>
        <button class="btn small danger" onclick="setStatus('${esc(e.canonical_key)}','Ditolak')">Tolak</button>`;
    } else if (st === 'Ditolak') {
      actions = `
        <input type="text" id="reason-${esc(e.canonical_key)}" placeholder="Alasan penolakan" value="${esc(e.reject_reason || '')}">
        <button class="btn small danger" onclick="setStatus('${esc(e.canonical_key)}','Ditolak',true)">Simpan Alasan</button>
        <button class="btn small primary" onclick="requeue('${esc(e.canonical_key)}')">Ajukan Ulang / Re-queue</button>`;
    } else if (st === 'Duplikat') {
      actions = '<span class="locked">🔒 Terkunci (0 Poin) · tidak ada Re-queue</span>';
    } else if (st === 'Valid') {
      actions = '<span class="locked">🔒 Final · +1 Poin</span>';
    }

    const reasonHtml = st === 'Ditolak' && e.reject_reason
      ? `<div class="q-meta"><span class="alive no">Alasan: ${esc(e.reject_reason)}</span></div>` : '';

    return `<div class="q-item">
      <div class="q-top">
        <div class="q-url">${esc(e.submit_url)}</div>
        <span class="badge ${badgeCls}">${esc(st)}</span>
      </div>
      <div class="q-key">canonical: ${esc(e.canonical_key)}</div>
      <div class="q-meta">
        <span>Skor: <strong>${e.score || 0}</strong></span>
        <span>Dibuat: ${fmtWib(e.created_at)}</span>
        ${e.submitted_at ? `<span>Submit: ${fmtWib(e.submitted_at)}</span>` : ''}
        ${locked ? '<span class="locked">🔒 Terkunci</span>' : ''}
        ${aliveHtml}
      </div>
      ${reasonHtml}
      <div class="q-actions">${actions}</div>
    </div>`;
  }).join('');
}

async function submitEntry(key) {
  const r = await api('/api/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ canonical_key: key }),
  });
  const url = r.entry.submit_url;
  const ok = await copyText(url);
  toast(ok ? `Tersalin: ${url} · ${fmtWib(r.entry.submitted_at)}` : `Gagal salin. URL: ${url}`, !ok);
  await loadQueue();
}

async function setStatus(key, status, withReason = false) {
  const payload = { canonical_key: key, status };
  if (withReason) payload.reason = ($(`reason-${key}`) || {}).value || '';
  try {
    await api('/api/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (status === 'Valid') toast('Valid · +1 poin');
    else toast('Status diperbarui');
    await loadQueue();
  } catch (err) { toast(err.message, true); }
}

async function requeue(key) {
  try {
    await api('/api/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ canonical_key: key, status: 'Siap Submit', reason: '' }),
    });
    toast('Dikembalikan ke antrean');
    await loadQueue();
  } catch (err) { toast(err.message, true); }
}

async function checkAlive(key) {
  const entry = entries.find((e) => e.canonical_key === key);
  if (!entry) return;
  toast('Mengecek…');
  try {
    const r = await api('/api/check-alive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: [entry.submit_url] }),
    });
    aliveMap[key] = r.results[0];
    renderQueue();
  } catch (err) { toast(err.message, true); }
}

/* ---------- backup / restore ---------- */
async function restoreJson(file) {
  if (!file) return;
  try {
    const text = await file.text();
    const r = await api('/api/import/json', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: text,
    });
    $('restore-status').textContent = `${r.imported} entri dipulihkan · ${fmtWib(new Date().toISOString())}`;
    toast(`Restore selesai: ${r.imported} entri`);
    await loadQueue();
  } catch (err) {
    $('restore-status').textContent = err.message;
    toast(err.message, true);
  }
}

/* ---------- init ---------- */
function init() {
  renderCountdown();
  setInterval(renderCountdown, 1000);

  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
      tab.classList.add('active');
      $(`panel-${tab.dataset.tab}`).classList.add('active');
    });
  });

  $('process-btn').addEventListener('click', processBulk);
  $('clear-input-btn').addEventListener('click', () => { $('bulk-input').value = ''; });
  $('subdomain-btn').addEventListener('click', searchSubdomains);
  $('add-subdomains-btn').addEventListener('click', addSubdomains);
  $('refresh-btn').addEventListener('click', loadQueue);
  $('status-filter').addEventListener('change', renderQueue);
  $('restore-input').addEventListener('change', (e) => restoreJson(e.target.files[0]));

  $('bulk-input').addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') processBulk();
  });

  loadQueue().catch((err) => toast(err.message, true));
}

document.addEventListener('DOMContentLoaded', init);
