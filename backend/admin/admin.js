
const API_BASE = (window.location.protocol === 'file:')
  ? 'http://localhost:8000'
  : ''; 

const $ = (s) => document.querySelector(s);

let selectedFile = null;
let parsedRows   = [];
let currentPage  = 1;
let totalPages   = 1;
const PER_PAGE   = 10;

/* ---------------- helpers ---------------- */
async function api(url, options = {}) {
  const res = await fetch(`${API_BASE}${url}`, { credentials: 'same-origin', ...options });
  if (!res.ok) {
    let msg = `خطای ${res.status}`;
    try { const j = await res.json(); msg = j.error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function esc(str) {
  return String(str).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------------- stats ---------------- */
async function loadStats() {
  try {
    const s = await api('/admin/api/stats');
    $('#statTotal').textContent    = s.total_documents;
    $('#statEmbedded').textContent = s.embedded_documents;
    $('#statDim').textContent      = s.dim ?? '–';
  } catch (e) { console.error(e); }
}

/* ---------------- file picking ---------------- */
const dropzone  = $('#dropzone');
const fileInput = $('#fileInput');

dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', e => {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', e => {
  if (e.target.files.length) handleFile(e.target.files[0]);
});

function handleFile(file) {
  if (!file.name.toLowerCase().endsWith('.json')) {
    showResult('err', 'فقط فایل با پسوند .json پذیرفته می‌شود.');
    return;
  }
  selectedFile = file;
  const reader = new FileReader();

  reader.onload = (ev) => {
    let data;
    try { data = JSON.parse(ev.target.result); }
    catch (err) {
      showResult('err', 'فایل JSON معتبر نیست: ' + err.message);
      parsedRows = [];
      $('#uploadBtn').disabled = true;
      $('#fileInfo').classList.remove('show');
      $('#preview').classList.remove('show');
      return;
    }

    if (!Array.isArray(data)) {
      showResult('err', 'ساختار فایل باید یک آرایه باشد: [{"question": "...", "answer": "..."}]');
      parsedRows = [];
      $('#uploadBtn').disabled = true;
      return;
    }

    const valid = [], problems = [];
    data.forEach((row, i) => {
      if (!row || typeof row !== 'object' || Array.isArray(row)) {
        problems.push(`ردیف ${i + 1}: باید یک آبجکت باشد`);
      } else if (typeof row.question !== 'string' || !row.question.trim()) {
        problems.push(`ردیف ${i + 1}: فیلد question خالی یا نامعتبر است`);
      } else if (typeof row.answer !== 'string' || !row.answer.trim()) {
        problems.push(`ردیف ${i + 1}: فیلد answer خالی یا نامعتبر است`);
      } else {
        valid.push({ question: row.question.trim(), answer: row.answer.trim() });
      }
    });

    parsedRows = valid;

    $('#fileInfo').innerHTML = `
      <b>${esc(file.name)}</b> — ${(file.size / 1024).toFixed(1)} کیلوبایت<br>
      ✅ ردیف‌های معتبر: <b>${valid.length}</b>
      ${problems.length ? ` &nbsp;|&nbsp; ⚠️ ردیف‌های نامعتبر: <b>${problems.length}</b>` : ''}
    `;
    $('#fileInfo').classList.add('show');

    if (valid.length) {
      const rows = valid.slice(0, 5).map(r => `
        <tr>
          <td class="q"><div class="ellip">${esc(r.question)}</div></td>
          <td class="a"><div class="ellip">${esc(r.answer)}</div></td>
        </tr>`).join('');
      $('#preview').innerHTML = `
        <table>
          <thead><tr><th>پرسش</th><th>پاسخ</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
      $('#preview').classList.add('show');
    } else {
      $('#preview').classList.remove('show');
    }

    $('#uploadBtn').disabled = valid.length === 0;
    clearResult();
    if (problems.length) {
      showResult('err', 'بعضی ردیف‌ها نامعتبر بودند و ارسال نخواهند شد:',
                 problems.slice(0, 8));
    }
  };

  reader.readAsText(file, 'utf-8');
}

/* ---------------- upload ---------------- */
$('#uploadBtn').addEventListener('click', async () => {
  if (!selectedFile) return;

  const btn = $('#uploadBtn');
  const original = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = 'در حال پردازش... <span class="spinner"></span>';
  clearResult();

  const fd = new FormData();
  fd.append('file', selectedFile, selectedFile.name);
  fd.append('replace', $('#replaceDupes').checked ? 'true' : 'false');

  try {
    const r = await api('/admin/api/upload', { method: 'POST', body: fd });
    showResult('ok',
      `✅ پردازش با موفقیت انجام شد (${r.duration_sec} ثانیه)`,
      [
        `ردیف‌های دریافتی: ${r.received}`,
        `درج‌شده جدید: ${r.inserted}`,
        `بازنویسی‌شده: ${r.updated}`,
        `تکراری (نادیده گرفته‌شده): ${r.skipped}`,
        `بردارسازی‌شده: ${r.embedded}`,
        ...(r.errors && r.errors.length ? [`خطاها: ${r.errors.length}`] : [])
      ],
      r.errors
    );
    loadStats();
    currentPage = 1;
    loadDocuments();
  } catch (e) {
    showResult('err', '❌ خطا: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
});

$('#clearBtn').addEventListener('click', () => {
  selectedFile = null;
  parsedRows = [];
  fileInput.value = '';
  $('#fileInfo').classList.remove('show');
  $('#preview').classList.remove('show');
  $('#uploadBtn').disabled = true;
  clearResult();
});

/* ---------------- result box ---------------- */
function clearResult() {
  const r = $('#result');
  r.className = 'result';
  r.innerHTML = '';
}
function showResult(type, title, bullets = [], details = []) {
  const r = $('#result');
  r.className = 'result show ' + type;
  let html = `<div>${esc(title)}</div>`;
  if (bullets.length) {
    html += '<ul>' + bullets.map(b => `<li>${esc(b)}</li>`).join('') + '</ul>';
  }
  if (details.length) {
    html += '<ul>' + details.slice(0, 10).map(d => `<li>${esc(d)}</li>`).join('') + '</ul>';
    if (details.length > 10)
      html += `<div style="margin-top:6px;font-size:12px;">... و ${details.length - 10} مورد دیگر</div>`;
  }
  r.innerHTML = html;
}

/* ---------------- documents list ---------------- */
async function loadDocuments() {
  const container = $('#docsContainer');
  const q = $('#searchInput').value.trim();

  try {
    const data = await api(
      `/admin/api/documents?page=${currentPage}&per_page=${PER_PAGE}&q=${encodeURIComponent(q)}`
    );
    totalPages = Math.max(1, Math.ceil(data.total / PER_PAGE));

    if (!data.items.length) {
      container.innerHTML = '<div class="empty">رکوردی یافت نشد.</div>';
    } else {
      container.innerHTML = `
        <table>
          <thead>
            <tr>
              <th style="width:50px">#</th>
              <th>پرسش</th>
              <th>پاسخ</th>
              <th style="width:90px">بردار</th>
              <th style="width:70px"></th>
            </tr>
          </thead>
          <tbody>
            ${data.items.map(d => `
              <tr>
                <td>${d.id}</td>
                <td class="q"><div class="ellip">${esc(d.question)}</div></td>
                <td class="a"><div class="ellip">${esc(d.answer)}</div></td>
                <td><span class="badge ${d.has_embedding ? 'yes' : 'no'}">
                      ${d.has_embedding ? 'دارد' : 'ندارد'}
                    </span></td>
                <td><button class="btn-danger" onclick="deleteDoc(${d.id})">حذف</button></td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    }

    $('#pageInfo').textContent = `صفحه ${currentPage} از ${totalPages}`;
    $('#prevBtn').disabled = currentPage <= 1;
    $('#nextBtn').disabled = currentPage >= totalPages;
  } catch (e) {
    container.innerHTML = `<div class="empty">خطا در بارگذاری: ${esc(e.message)}</div>`;
  }
}

async function deleteDoc(id) {
  if (!confirm(`رکورد #${id} حذف شود؟`)) return;
  try {
    await api(`/admin/api/documents/${id}`, { method: 'DELETE' });
    loadDocuments();
    loadStats();
  } catch (e) {
    alert('خطا در حذف: ' + e.message);
  }
}

$('#prevBtn').addEventListener('click', () => { if (currentPage > 1) { currentPage--; loadDocuments(); } });
$('#nextBtn').addEventListener('click', () => { if (currentPage < totalPages) { currentPage++; loadDocuments(); } });
$('#searchBtn').addEventListener('click', () => { currentPage = 1; loadDocuments(); });
$('#searchInput').addEventListener('keypress', e => {
  if (e.key === 'Enter') { currentPage = 1; loadDocuments(); }
});

/* ---------------- init ---------------- */
loadStats();
loadDocuments();