const BACKEND_URL = 'http://localhost:8000';
const API = BACKEND_URL + '/admin/api/chat';

const state = {
    sessions: [],
    current: null,
    timer: null,
    q: ''
};

const els = {
    list: document.getElementById('sessionList'),
    thread: document.getElementById('thread'),
    title: document.getElementById('chatTitle'),
    meta: document.getElementById('chatMeta'),
    badges: document.getElementById('chatBadges'),
    search: document.getElementById('sessionSearch'),
    refresh: document.getElementById('refreshBtn'),
    auto: document.getElementById('autoRefresh'),
    statsRow: document.getElementById('statsRow')
};

const dateFmt = new Intl.DateTimeFormat('fa-IR', { dateStyle: 'short', timeStyle: 'short' });

function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

function fmtDate(value) {
    if (!value) return '—';
    const d = new Date(String(value).replace(' ', 'T'));
    return isNaN(d.getTime()) ? String(value) : dateFmt.format(d);
}

function fmtNum(n) {
    return Number(n || 0).toLocaleString('fa-IR');
}

async function api(path) {
    const res = await fetch(API + path, {
        headers: { Accept: 'application/json' },
        credentials: 'include'   // send cookies/session if the backend uses them
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
}

/* ------------------------- Sessions ------------------------- */

async function loadSessions() {
    try {
        const data = await api('/sessions?q=' + encodeURIComponent(state.q));
        state.sessions = data.sessions || [];
        renderSessions();
    } catch (err) {
        console.error(err);
        els.list.innerHTML = '<div class="empty">خطا در دریافت فهرست گفتگوها</div>';
    }
}

function renderSessions() {
    els.list.innerHTML = '';

    if (!state.sessions.length) {
        els.list.innerHTML = '<div class="empty">گفتگویی یافت نشد</div>';
        return;
    }

    const frag = document.createDocumentFragment();

    state.sessions.forEach(function (s) {
        const isActive = state.current
            && state.current.user_id === s.user_id
            && state.current.ip_address === s.ip_address;

        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'session-item' + (isActive ? ' active' : '');

        let badges = '<span class="badge">' + fmtNum(s.message_count) + ' پیام</span>';
        if (s.likes > 0) {
            badges += '<span class="badge like">👍 ' + fmtNum(s.likes) + '</span>';
        }
        if (s.dislikes > 0) {
            badges += '<span class="badge dislike">👎 ' + fmtNum(s.dislikes) + '</span>';
        }

        item.innerHTML =
            '<div class="session-top">' +
                '<span class="session-id">' + esc(s.user_id) + '</span>' +
                '<span class="session-time">' + esc(fmtDate(s.last_activity)) + '</span>' +
            '</div>' +
            '<div class="session-ip">IP: ' + esc(s.ip_address) + '</div>' +
            '<div class="session-preview">' + esc(s.first_message || '—') + '</div>' +
            '<div class="session-badges">' + badges + '</div>';

        item.addEventListener('click', function () {
            selectSession(s);
        });

        frag.appendChild(item);
    });

    els.list.appendChild(frag);
}

/* ------------------------- Stats ------------------------- */

async function loadStats() {
    try {
        const data = await api('/stats');
        els.statsRow.innerHTML =
            '<div class="stat"><span class="stat-value">' + fmtNum(data.total_sessions) + '</span><span class="stat-label">گفتگو</span></div>' +
            '<div class="stat"><span class="stat-value">' + fmtNum(data.total_messages) + '</span><span class="stat-label">پیام</span></div>' +
            '<div class="stat"><span class="stat-value">' + fmtNum(data.likes) + '</span><span class="stat-label">👍</span></div>' +
            '<div class="stat"><span class="stat-value">' + fmtNum(data.dislikes) + '</span><span class="stat-label">👎</span></div>';
    } catch (err) {
        console.error(err);
        els.statsRow.innerHTML = '';
    }
}

/* ------------------------- Thread ------------------------- */

async function selectSession(session) {
    state.current = session;

    els.title.textContent = session.user_id;
    els.meta.textContent = 'IP: ' + session.ip_address + '  •  شروع: ' + fmtDate(session.started_at);
    els.badges.innerHTML =
        '<span class="badge">' + fmtNum(session.message_count) + ' پیام</span>' +
        (session.likes > 0 ? '<span class="badge like">👍 ' + fmtNum(session.likes) + '</span>' : '') +
        (session.dislikes > 0 ? '<span class="badge dislike">👎 ' + fmtNum(session.dislikes) + '</span>' : '');

    renderSessions();
    els.thread.innerHTML = '<div class="loading">در حال بارگذاری پیام‌ها…</div>';

    await loadMessages(false);
}

async function loadMessages(silent) {
    if (!state.current) return;

    const s = state.current;
    const path = '/messages?user_id=' + encodeURIComponent(s.user_id) +
                 '&ip=' + encodeURIComponent(s.ip_address);

    try {
        const data = await api(path);
        renderThread(data.messages || []);
    } catch (err) {
        console.error(err);
        if (!silent) {
            els.thread.innerHTML = '<div class="empty-state">خطا در دریافت پیام‌ها</div>';
        }
    }
}

function bubble(text, kind, time, feedback) {
    const wrap = document.createElement('div');
    wrap.className = 'message ' + (kind === 'user' ? 'user-message' : 'bot-message');

    const b = document.createElement('div');
    b.className = 'bubble';
    b.textContent = text || '';

    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = fmtDate(time);

    wrap.appendChild(b);
    wrap.appendChild(meta);

    if (kind === 'bot' && feedback) {
        const tag = document.createElement('span');
        tag.className = 'feedback-tag ' + feedback;
        tag.textContent = feedback === 'like' ? '👍 مفید' : '👎 غیرمفید';
        wrap.appendChild(tag);
    }

    return wrap;
}

function renderThread(messages) {
    els.thread.innerHTML = '';

    if (!messages.length) {
        els.thread.innerHTML = '<div class="empty-state">پیامی برای این گفتگو ثبت نشده است.</div>';
        return;
    }

    const frag = document.createDocumentFragment();

    messages.forEach(function (m) {
        const group = document.createElement('div');
        group.className = 'msg-group';
        group.appendChild(bubble(m.user_message, 'user', m.created_at, null));
        group.appendChild(bubble(m.bot_response, 'bot', m.created_at, m.feedback));
        frag.appendChild(group);
    });

    els.thread.appendChild(frag);
    els.thread.scrollTop = els.thread.scrollHeight;
}

/* ------------------------- Auto refresh ------------------------- */

function setupAutoRefresh() {
    if (state.timer) {
        clearInterval(state.timer);
        state.timer = null;
    }

    if (els.auto.checked) {
        state.timer = setInterval(function () {
            loadSessions();
            loadStats();
            if (state.current) {
                loadMessages(true);
            }
        }, 10000);
    }
}

/* ------------------------- Events ------------------------- */

let searchTimer = null;

els.search.addEventListener('input', function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
        state.q = els.search.value.trim();
        loadSessions();
    }, 300);
});

els.refresh.addEventListener('click', function () {
    loadSessions();
    loadStats();
    if (state.current) {
        loadMessages(false);
    }
});

els.auto.addEventListener('change', setupAutoRefresh);

/* ------------------------- Init ------------------------- */

loadSessions();
loadStats();
setupAutoRefresh();