/* Workspace riset solo. Tanpa framework, tanpa dekorasi. */

const $ = (id) => document.getElementById(id);

const JADWAL = [
  { label: 'Pendaftaran dibuka', tanggal: 'Rab, 25 Nov 2026, 00.00 WIB', iso: '2026-11-25T00:00:00+07:00' },
  { label: 'Pendaftaran ditutup', tanggal: 'Jum, 4 Des 2026, 00.00 WIB', iso: '2026-12-04T00:00:00+07:00' },
  { label: 'Hackaton dimulai (Submit link dibuka)', tanggal: 'Sab, 5 Des 2026, 00.00 WIB', iso: '2026-12-05T00:00:00+07:00' },
  { label: 'Batas submit', tanggal: 'Kam, 10 Des 2026, 00.00 WIB', iso: '2026-12-10T00:00:00+07:00' },
  { label: 'Pengumuman pemenang', tanggal: 'Sen, 14 Des 2026, 00.00 WIB', iso: '2026-12-14T00:00:00+07:00' },
  { label: 'Survei peserta ditutup', tanggal: 'Min, 27 Des 2026, 00.00 WIB', iso: '2026-12-27T00:00:00+07:00' },
];

const STATUS_KELAS = {
  'Siap Submit': 'Siap_Submit',
  'Menunggu Penilaian': 'Menunggu_Penilaian',
  'Valid': 'Valid',
  'Duplikat': 'Duplikat',
  'Ditolak': 'Ditolak',
};

let entri = [];
let kandidatOsint = [];
let aliveMap = {};
let liveTimer = null;
let liveController = null;
let liveNomor = 0;
let tembakan = [];

/* ---------- dasar ---------- */
async function api(url, opsi = {}) {
  const res = await fetch(url, opsi);
  let data;
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function esc(nilai) {
  const d = document.createElement('div');
  d.textContent = nilai == null ? '' : String(nilai);
  return d.innerHTML;
}

function pesan(teks, gagal = false) {
  const bar = $('statusline');
  bar.textContent = teks;
  bar.classList.toggle('error', !!gagal);
}

async function salin(teks) {
  try {
    await navigator.clipboard.writeText(teks);
    return true;
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = teks;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  }
}

function waktuWib(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat('id-ID', {
    timeZone: 'Asia/Jakarta', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(d) + ' WIB';
}

/* ---------- jam dan jadwal ---------- */
function updateJam() {
  $('clock').textContent = waktuWib(new Date().toISOString());

  const sekarang = Date.now();
  let berikutnya = true;
  JADWAL.forEach((j) => {
    const el = document.querySelector(`[data-jadwal="${j.iso}"]`);
    if (!el) return;
    const sisa = new Date(j.iso).getTime() - sekarang;
    if (sisa <= 0) {
      el.textContent = 'terlewat';
      el.closest('.schedule-item').classList.add('done');
      return;
    }
    const hari = Math.floor(sisa / 86400000);
    const jam = Math.floor((sisa % 86400000) / 3600000);
    const menit = Math.floor((sisa % 3600000) / 60000);
    const detik = Math.floor((sisa % 60000) / 1000);
    const sisaTeks = `${hari}h ${String(jam).padStart(2, '0')}j ${String(menit).padStart(2, '0')}m ${String(detik).padStart(2, '0')}d`;
    el.textContent = berikutnya ? `Berikutnya · ${sisaTeks}` : sisaTeks;
    berikutnya = false;
  });
}

function renderJadwal() {
  $('schedule').innerHTML = JADWAL.map((j) => `
    <div class="schedule-item">
      <div class="schedule-label">${esc(j.label)}</div>
      <div class="schedule-date">${esc(j.tanggal)}</div>
      <div class="schedule-left" data-jadwal="${j.iso}">-</div>
    </div>
  `).join('');
}

/* ---------- angka dan antrean ---------- */
async function muatAngka() {
  const s = await api('/api/stats');
  $('m-score').textContent = s.total_score ?? 0;
  $('m-waiting').textContent = s['Menunggu Penilaian'] ?? 0;
  $('m-ready').textContent = s['Siap Submit'] ?? 0;
  $('m-rejected').textContent = s.Ditolak ?? 0;
  $('m-duplicate').textContent = s.Duplikat ?? 0;
}

async function muatEntri() {
  const r = await api('/api/entries');
  entri = (r.entries || []).slice().sort((a, b) => peringkat(a) - peringkat(b));
  renderAntrean();
  renderRapid();
  await muatAngka();
}

function peringkat(e) {
  const p = e.probe || {};
  if (p.parking || p.alive === false) return 2;
  if (p.alive === true) return 0;
  return 1;
}

function renderAntrean() {
  const filter = $('status-filter').value;
  const daftar = filter === 'all' ? entri : entri.filter((e) => e.status === filter);
  const body = $('queue-body');

  if (!daftar.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">Tidak ada entri pada filter ini.</td></tr>';
    return;
  }

  body.innerHTML = daftar.map((e) => {
    const k = e.canonical_key;
    const st = e.status;
    const kelas = STATUS_KELAS[st] ? STATUS_KELAS[st].replace(/\s+/g, '_') : 'Siap_Submit';
    const hidup = e.probe || aliveMap[k];
    const hidupKelas = hidup && hidup.parking ? '' : (hidup && hidup.alive ? 'yes' : 'no');
    let hidupHtml = hidup
      ? `<div class="alive ${hidupKelas}">${esc(hidup.label)}${hidup.title ? ` · ${esc(hidup.title)}` : ''}</div>`
      : '';
    if (hidup && hidup.redirect_key && hidup.redirect_target) {
      hidupHtml += `<div class="hint">Redirect: ${esc(hidup.redirect_target)}</div>`
        + `<button class="btn" data-action="redirect" data-key="${esc(hidup.redirect_key)}" data-url="${esc(hidup.redirect_target)}">Impor Redirect</button>`;
    }
    let aksi = '';

    if (st === 'Siap Submit') {
      aksi = `
        <button class="btn" data-action="cek" data-key="${esc(k)}">Cek Hidup</button>
        <button class="btn primary" data-action="submit" data-key="${esc(k)}">Salin &amp; Tandai Submitted</button>
        <button class="btn" data-action="status" data-target="Duplikat" data-key="${esc(k)}">Tandai Duplikat</button>`;
    } else if (st === 'Menunggu Penilaian') {
      aksi = `
        <button class="btn primary" data-action="status" data-target="Valid" data-key="${esc(k)}">Tandai Valid</button>
        <button class="btn" data-action="status" data-target="Duplikat" data-key="${esc(k)}">Tandai Duplikat</button>
        <button class="btn" data-action="status" data-target="Ditolak" data-key="${esc(k)}">Tolak</button>`;
    } else if (st === 'Ditolak') {
      aksi = `
        <input type="text" data-alasan="${esc(k)}" placeholder="Alasan penolakan" value="${esc(e.reject_reason || '')}">
        <button class="btn" data-action="alasan" data-key="${esc(k)}">Simpan Alasan</button>
        <button class="btn primary" data-action="status" data-target="Siap Submit" data-key="${esc(k)}">Ajukan Ulang / Re-queue</button>`;
    } else {
      aksi = `<span class="locked">Terkunci permanen${st === 'Valid' ? ' · +1 poin' : ' · 0 poin'}</span>`;
    }

    return `<tr>
      <td class="mono">${esc(e.submit_url)}<div class="hint">asal: ${esc(e.raw || '-')}</div></td>
      <td class="mono">${esc(k)}</td>
      <td><span class="status ${kelas}">${esc(st)}</span></td>
      <td class="mono">${waktuWib(e.submitted_at || e.created_at)}</td>
      <td class="mono score">${e.score || 0}</td>
      <td><div class="cell-actions">${aksi}${hidupHtml}</div></td>
    </tr>`;
  }).join('');
}

/* ---------- mode tembak cepat ---------- */
function entriTembak() {
  return entri.find((e) => e.status === 'Siap Submit') || null;
}

function renderRapid() {
  const e = entriTembak();
  const btn = $('rapid-btn');
  $('rapid-url').textContent = e ? e.submit_url : 'Tidak ada entri Siap Submit.';
  $('rapid-key').textContent = e ? e.canonical_key : '';
  $('rapid-status').textContent = e
    ? (e.probe ? e.probe.label : 'Belum dicek')
    : '';
  $('rapid-title').textContent = e && e.probe && e.probe.title ? `Judul: ${e.probe.title}` : '';
  btn.disabled = !e;

  const menit = tembakan.length > 1
    ? (Date.now() - tembakan[0]) / 60000
    : 0;
  $('rapid-count').textContent = tembakan.length;
  const laju = menit > 0 ? Math.max(0, tembakan.length - 1) / menit : 0;
  $('rapid-rate').textContent = `${laju.toFixed(1)} entri/menit`;
  $('rapid-last').textContent = tembakan.length ? waktuWib(new Date(tembakan[tembakan.length - 1]).toISOString()) : '-';
}

async function tembakCepat() {
  const e = entriTembak();
  if (!e) return pesan('Antrean Siap Submit kosong.', true);
  try {
    const r = await api('/api/submit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ canonical_key: e.canonical_key }),
    });
    const ok = await salin(r.entry.submit_url);
    tembakan.push(Date.now());
    if (tembakan.length > 200) tembakan.shift();
    pesan(`${ok ? 'URL tersalin' : 'Salin gagal'}: ${r.entry.submit_url} · ${waktuWib(r.entry.submitted_at)}`, !ok);
    await muatEntri();
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

/* ---------- aksi antrean ---------- */
async function aksiSubmit(key) {
  const r = await api('/api/submit', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ canonical_key: key }),
  });
  const ok = await salin(r.entry.submit_url);
  pesan(`${ok ? 'URL tersalin' : 'Salin gagal'}: ${r.entry.submit_url} · ${waktuWib(r.entry.submitted_at)}`, !ok);
  await muatEntri();
}

async function aksiStatus(key, target) {
  const payload = { canonical_key: key, status: target };
  if (target === 'Ditolak') {
    const input = document.querySelector(`[data-alasan="${CSS.escape(key)}"]`);
    payload.reason = input ? input.value : '';
  }
  try {
    await api('/api/status', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    pesan(target === 'Valid' ? 'Status Valid. Skor bertambah 1.' : `Status menjadi ${target}.`);
    await muatEntri();
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

async function aksiAlasan(key) {
  const input = document.querySelector(`[data-alasan="${CSS.escape(key)}"]`);
  try {
    await api('/api/status', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ canonical_key: key, status: 'Ditolak', reason: input ? input.value : '' }),
    });
    pesan('Alasan penolakan tersimpan.');
    await muatEntri();
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

async function aksiCek(key) {
  const e = entri.find((x) => x.canonical_key === key);
  if (!e) return;
  pesan(`Mengecek ${e.submit_url}...`);
  try {
    const r = await api('/api/check-alive', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: [e.submit_url] }),
    });
    aliveMap[key] = r.results[0];
    await muatEntri();
    pesan(`Hasil: ${r.results[0].label}`);
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

async function imporRedirect(key, url) {
  try {
    const r = await api('/api/process', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: [url || key] }),
    });
    pesan(`Redirect ${key} masuk antrean: ${r.unique_count}.`);
    await muatEntri();
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

/* ---------- sinkronisasi massal panitia ---------- */
async function sinkronPanitia() {
  const teks = $('sync-input').value.trim();
  if (!teks) return pesan('Tempel baris riwayat panitia terlebih dahulu.', true);
  pesan('Mencocokkan riwayat panitia...');
  try {
    const r = await api('/api/sync-panitia', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: teks }),
    });
    $('sync-status').textContent = `${r.terupdate} diperbarui · ${r.cocok} cocok · ${r.dilewati} sudah sesuai · ${r.tidak_cocok} tidak cocok · ${r.gagal} ditolak.`;
    $('sync-results').innerHTML = r.rincian.length
      ? r.rincian.map((d) => `<div class="row"><span class="key">${esc(d.canonical_key)}</span><span>${esc(d.status)}</span><span class="${d.hasil === 'Diperbarui' ? 'key' : 'why'}">${esc(d.hasil)}</span></div>`).join('')
      : '<div class="row-empty">Tidak ada baris status yang dikenali.</div>';
    pesan(`Sinkronisasi selesai: ${r.terupdate} entri diperbarui.`);
    await muatEntri();
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

/* ---------- bulk cleaner ---------- */
async function prosesBulk() {
  const urls = $('bulk-input').value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return pesan('Tempel URL atau domain terlebih dahulu.', true);

  pesan(`Memproses ${urls.length} entri...`);
  try {
    const r = await api('/api/process', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls }),
    });
    $('count-unique').textContent = r.unique_count;
    $('count-duplicate').textContent = r.duplicate_count;
    $('count-invalid').textContent = r.invalid_count;

    $('result-unique').innerHTML = r.uniques.length
      ? r.uniques.map((e) => `<div class="row"><div>${esc(e.submit_url)}</div><div class="key">${esc(e.canonical_key)}</div></div>`).join('')
      : '<div class="row-empty">Tidak ada</div>';
    $('result-duplicate').innerHTML = r.duplicates.length
      ? r.duplicates.map((e) => `<div class="row">${esc(e.raw)}<div class="why">${esc(e.reason)}</div></div>`).join('')
      : '<div class="row-empty">Tidak ada</div>';
    $('result-invalid').innerHTML = r.invalids.length
      ? r.invalids.map((e) => `<div class="row">${esc(e.raw)}<div class="why">${esc(e.reason)}</div></div>`).join('')
      : '<div class="row-empty">Tidak ada</div>';

    pesan(`${r.unique_count} unik masuk antrean. ${r.duplicate_count} duplikat. ${r.invalid_count} tidak sah.`);
    await muatEntri();
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

/* ---------- OSINT pasif ---------- */
function setKandidat(daftar) {
  kandidatOsint = daftar;
  $('osint-import-btn').disabled = daftar.length === 0;
}

function tampilkanKandidat(daftar) {
  $('osint-batch-list').innerHTML = daftar.length
    ? daftar.map((s) => `<span class="chip">${esc(s)}</span>`).join('')
    : '';
  setKandidat(daftar);
}

function statusLive(teks) {
  $('osint-single-status').textContent = teks || '';
}

function pesanLive(error) {
  const teks = String(error || '');
  if (/timed out|Koneksi ke (?:crt\.sh|urlscan\.io)|HTTP 5\d\d|Permintaan ke (?:crt\.sh|urlscan\.io)|urlscan\.io|Failed to fetch/i.test(teks)) {
    return 'Sumber penemuan sedang sibuk. Coba lagi sebentar.';
  }
  return teks || 'Pencarian gagal.';
}

function renderDetailLive(detail) {
  if (!detail.length) {
    $('osint-single-list').innerHTML = '<div class="row-empty">Tidak ada kandidat tercatat.</div>';
    return;
  }
  $('osint-single-list').innerHTML = detail.map((d) => {
    const baru = d.status === 'Baru (Siap Masuk Antrean)';
    return `<div class="row"><span class="key">${esc(d.canonical_key)}</span><span class="${baru ? 'key' : 'why'}">${esc(d.status)}</span></div>`;
  }).join('');
}

function jadwalCariLive() {
  clearTimeout(liveTimer);
  if (liveController) liveController.abort();
  const teks = $('osint-single').value.trim();
  if (teks.length < 4) {
    setKandidat([]);
    $('osint-single-list').innerHTML = '';
    statusLive(teks ? 'Minimal 4 karakter untuk kata kunci.' : '');
    return;
  }
  statusLive('Menunggu jeda ketik...');
  liveTimer = setTimeout(() => cariLive(teks), 900);
}

async function imporOtomatis(daftar, nomor) {
  const r = await api('/api/osint/import', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls: daftar }),
  });
  if (nomor !== liveNomor) return null;
  await muatEntri();
  return r.added;
}

async function cariLive(teks) {
  clearTimeout(liveTimer);
  if (liveController) liveController.abort();
  liveController = new AbortController();
  const nomor = ++liveNomor;
  statusLive('Mencari di crt.sh + urlscan.io...');
  try {
    const r = await api(`/api/subdomains?q=${encodeURIComponent(teks)}`, {
      signal: liveController.signal,
    });
    if (nomor !== liveNomor) return;

    if (!r.ok) {
      setKandidat([]);
      $('osint-single-list').innerHTML = '';
      statusLive(pesanLive(r.error));
      return;
    }

    setKandidat(r.baru || []);
    renderDetailLive(r.detail || []);
    const cache = r.dari_cache ? ' · cache 5 menit' : '';
    const sumber = (r.sumber || []).length ? ` dari ${r.sumber.join(' + ')}` : '';
    statusLive(`Ditemukan ${r.baru.length} kandidat baru${sumber}${cache}.`);

    if ($('osint-auto-import').checked && r.baru.length) {
      const ditambahkan = await imporOtomatis(r.baru, nomor);
      if (nomor !== liveNomor || ditambahkan === null) return;
      renderDetailLive((r.detail || []).map((d) => ({
        ...d, status: 'Sudah Ada di Tracker',
      })));
      setKandidat([]);
      statusLive(`Ditemukan ${r.baru.length} kandidat baru. Otomatis masuk antrean: ${ditambahkan}.`);
    }
  } catch (err) {
    if (err.name === 'AbortError' || nomor !== liveNomor) return;
    setKandidat([]);
    $('osint-single-list').innerHTML = '';
    statusLive(pesanLive(err.message));
  }
}

async function cariSatu() {
  clearTimeout(liveTimer);
  const teks = $('osint-single').value.trim();
  if (teks.length < 4) {
    setKandidat([]);
    $('osint-single-list').innerHTML = '';
    statusLive('Minimal 4 karakter untuk kata kunci.');
    return;
  }
  return cariLive(teks);
}

async function scanBenih() {
  const seeds = $('seeds-input').value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  if (!seeds.length) return pesan('Isi daftar benih terlebih dahulu.', true);
  pesan(`Memindai ${seeds.length} benih...`);
  try {
    const r = await api('/api/osint/scan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seeds }),
    });
    $('osint-batch-status').textContent = `${r.total} kandidat unik dari ${r.seeds_count} benih. Gagal: ${r.error_count}`;
    $('osint-batch-results').innerHTML = r.results.map((x) => `
      <div class="row">${esc(x.seed)} <span class="why">${esc(x.sumber || '')}</span>
        <div class="${x.ok ? 'key' : 'why'}">${x.ok ? `${x.count} kandidat` : `Gagal: ${esc(x.error)}`}</div>
      </div>
    `).join('');
    tampilkanKandidat(r.candidates);
    pesan(`Pemindaian selesai. ${r.total} kandidat unik.`);
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

async function imporKandidat() {
  if (!kandidatOsint.length) return;
  pesan('Memasukkan kandidat ke antrean...');
  try {
    const r = await api('/api/osint/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: kandidatOsint }),
    });
    pesan(`Masuk antrean: ${r.added}. Dilewati: ${r.skipped_existing}. Tidak sah: ${r.invalid}.`);
    await muatEntri();
  } catch (err) { pesan(`Gagal: ${err.message}`, true); }
}

async function muatContoh() {
  try {
    const r = await api('/api/seeds/example');
    $('seeds-input').value = r.text;
    pesan(r.ok ? 'Contoh benih dimuat.' : `Contoh benih tidak dapat dibaca: ${r.error}`);
  } catch (err) {
    pesan(`Gagal memuat contoh benih: ${err.message}`, true);
  }
}

/* ---------- backup ---------- */
async function restore(file) {
  if (!file) return;
  try {
    const teks = await file.text();
    const r = await api('/api/import/json', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: teks,
    });
    $('restore-status').textContent = `${r.imported} entri dipulihkan.`;
    pesan(`Restore selesai: ${r.imported} entri.`);
    await muatEntri();
  } catch (err) {
    $('restore-status').textContent = err.message;
    pesan(`Gagal: ${err.message}`, true);
  }
}

async function muatStatusRutinitas() {
  try {
    const r = await api('/api/rutinitas/status');
    $('rutinitas-status').textContent = r.backup_terakhir
      ? `Backup terakhir: ${r.backup_terakhir}`
      : 'Backup terakhir: belum ada';
    $('rutinitas-log').textContent = r.log_terakhir || '';
  } catch (err) {
    $('rutinitas-status').textContent = `Gagal memuat status rutinitas: ${err.message}`;
  }
}

/* ---------- awal ---------- */
function init() {
  renderJadwal();
  updateJam();
  setInterval(updateJam, 1000);

  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
      tab.classList.add('active');
      $(`view-${tab.dataset.tab}`).classList.add('active');
    });
  });

  $('process-btn').addEventListener('click', prosesBulk);
  $('clear-btn').addEventListener('click', () => { $('bulk-input').value = ''; pesan('Kolom input dikosongkan.'); });
  $('osint-single').addEventListener('input', jadwalCariLive);
  $('osint-single').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); cariSatu(); }
  });
  $('osint-single-btn').addEventListener('click', cariSatu);
  $('osint-auto-import').addEventListener('change', (e) => {
    statusLive(e.target.checked
      ? 'Saklar aktif. Kandidat baru berikutnya akan otomatis masuk antrean.'
      : 'Saklar dimatikan. Impor tetap manual.');
  });
  $('osint-batch-btn').addEventListener('click', scanBenih);
  $('osint-import-btn').addEventListener('click', imporKandidat);
  $('load-example-btn').addEventListener('click', muatContoh);
  $('refresh-btn').addEventListener('click', muatEntri);
  $('rapid-btn').addEventListener('click', tembakCepat);
  $('sync-btn').addEventListener('click', sinkronPanitia);
  $('sync-clear-btn').addEventListener('click', () => {
    $('sync-input').value = '';
    $('sync-status').textContent = '';
    $('sync-results').innerHTML = '';
    pesan('Kolom riwayat panitia dikosongkan.');
  });
  $('rutinitas-refresh-btn').addEventListener('click', muatStatusRutinitas);
  $('status-filter').addEventListener('change', renderAntrean);
  $('restore-input').addEventListener('change', (e) => restore(e.target.files[0]));

  document.addEventListener('keydown', (e) => {
    if (!e.altKey || e.key.toLowerCase() !== 's' || e.ctrlKey || e.metaKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable) return;
    e.preventDefault();
    tembakCepat();
  });

  $('queue-body').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const key = btn.dataset.key;
    if (btn.dataset.action === 'submit') aksiSubmit(key);
    if (btn.dataset.action === 'status') aksiStatus(key, btn.dataset.target);
    if (btn.dataset.action === 'alasan') aksiAlasan(key);
    if (btn.dataset.action === 'cek') aksiCek(key);
    if (btn.dataset.action === 'redirect') imporRedirect(key, btn.dataset.url);
  });

  $('bulk-input').addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') prosesBulk();
  });

  muatEntri().catch((err) => pesan(`Gagal memuat data: ${err.message}`, true));
  muatStatusRutinitas();
}

document.addEventListener('DOMContentLoaded', init);
