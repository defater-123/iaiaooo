/* ============================================================
   VPS MANAGER — клиентская логика сайта управления сервером
   Работает в связке с web/server.py (JSON API + SSE-стриминг)
   ============================================================ */

'use strict';

// ---------------- Глобальное состояние ----------------
const TOKEN_KEY = 'vps_session_token';
let token = localStorage.getItem(TOKEN_KEY) || '';
let currentPath = '.';          // относительный путь от корня файлов сервера
let expiresAt = null;
let newKind = 'file';
let editorPath = '';
let termHistory = [];
let histIdx = -1;
let statsTimer = null;

const $ = (id) => document.getElementById(id);

// ---------------- API-хелперы ----------------
async function api(endpoint, { method = 'GET', body = null } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);
    try {
        const r = await fetch('/api/' + endpoint, opts);
        const data = await r.json().catch(() => ({}));
        if (r.status === 401 && endpoint !== 'activate') {
            logout();
        }
        return data;
    } catch (e) {
        return { error: 'Нет связи с сервером' };
    }
}

function toast(text, type = '') {
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = text;
    $('toastWrap').appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

function copyText(text) {
    navigator.clipboard.writeText(text)
        .then(() => toast('Скопировано: ' + text, 'success'))
        .catch(() => prompt('Скопируйте вручную:', text));
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
}

function fmtSize(b) {
    if (!b) return '0 B';
    const k = 1024, u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(b) / Math.log(k));
    return parseFloat((b / Math.pow(k, i)).toFixed(1)) + ' ' + u[i];
}

function fmtUptime(min) {
    if (!min && min !== 0) return '—';
    if (min < 60) return min + 'м';
    const h = Math.floor(min / 60), m = min % 60;
    if (h < 24) return `${h}ч ${m}м`;
    return `${Math.floor(h / 24)}д ${h % 24}ч`;
}

// ---------------- Авторизация ----------------
async function activate() {
    const code = $('codeInput').value.trim().toUpperCase();
    $('authError').textContent = '';
    if (code.length < 4) {
        $('authError').textContent = 'Введите код из логов GitHub Actions';
        return;
    }
    $('activateBtn').disabled = true;
    const res = await api('activate', { method: 'POST', body: { code } });
    $('activateBtn').disabled = false;
    if (res.error) {
        $('authError').textContent = res.error;
        return;
    }
    token = res.token;
    localStorage.setItem(TOKEN_KEY, token);
    enterApp(res.ip, res.password, res.expires);
}

function logout(msg) {
    token = '';
    localStorage.removeItem(TOKEN_KEY);
    clearInterval(statsTimer);
    $('app').classList.remove('active');
    $('authScreen').classList.remove('hidden');
    if (msg) $('authError').textContent = msg;
}

async function enterApp(ip, password, expires) {
    $('authScreen').classList.add('hidden');
    $('app').classList.add('active');

    if (ip) {
        $('ipText').textContent = ip;
        $('sIp').textContent = ip;
        $('sshCmd').textContent = 'ssh runner@' + ip;
    }
    if (password) $('sPass').textContent = password;
    if (expires) setExpiry(expires);

    loadStats();
    loadSessionInfo();
    statsTimer = setInterval(loadStats, 5000);
    welcomeTerminal();
}

function setExpiresFromServer(expires) {
    if (expires) setExpiry(expires);
}

function setExpiry(iso) {
    // Разбираем ISO с часовым поясом — иначе браузер считает время локальным и таймер «истёк» врёт
    let d = new Date(iso);
    if (!iso.includes('+') && !iso.endsWith('Z')) {
        d = new Date(iso + '+00:00'); // серверные времена без offset трактуем как UTC
    }
    expiresAt = d;
    const el = $('iExpires');
    if (el) el.textContent = d.toLocaleString('ru-RU');
    tickTimer();
}

async function loadSessionInfo() {
    const s = await api('session');
    if (!s.trusted) return;
    setExpiry(s.expires);
    $('ipText').textContent = s.ip;
    $('sIp').textContent = s.ip;
    $('sPass').textContent = s.password;
    $('sshCmd').textContent = s.ssh;
}

function tickTimer() {
    if (!expiresAt) return;
    const ms = expiresAt - Date.now();
    if (ms <= 0) {
        $('timerText').textContent = 'истёк';
        setStatus(false);
        clearInterval(statsTimer);
        return;
    }
    const h = Math.floor(ms / 3600000);
    const m = Math.floor(ms % 3600000 / 60000);
    const sec = Math.floor(ms % 60000 / 1000);
    $('timerText').textContent = `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
}
setInterval(tickTimer, 1000);

function setStatus(ok) {
    const pill = $('statusPill');
    pill.classList.toggle('offline', !ok);
    $('statusText').textContent = ok ? 'онлайн' : 'нет доступа';
}

// ---------------- Дашборд: статистика ----------------
async function loadStats() {
    const st = await api('stats');
    if (st.error) return;

    const cpuNum = parseFloat(st.cpu) || 0;
    $('mCpu').textContent = st.cpu;
    $('cpuBar').style.width = Math.min(cpuNum, 100) + '%';
    $('coresHint').textContent = 'ядер: ' + (st.cores || '—');

    $('mRam').textContent = st.ram_used ? st.ram_used + ' МБ' : '—';
    if (st.ram_total && st.ram_used) {
        $('ramBar').style.width = Math.min(st.ram_used / st.ram_total * 100, 100) + '%';
        $('ramHint').textContent = 'из ' + st.ram_total + ' МБ';
    }

    const diskNum = parseFloat(st.disk) || 0;
    $('mDisk').textContent = st.disk;
    $('diskBar').style.width = Math.min(diskNum, 100) + '%';
    $('diskHint').textContent = st.disk_used + ' / ' + st.disk_total;

    $('mUptime').textContent = fmtUptime(st.uptime_min);
    $('timeHint').textContent = st.time || '—';
    if (st.expires) setExpiry(st.expires);

    $('iOs').textContent = st.os || '—';
    $('iKernel').textContent = st.kernel || '—';
    $('iHost').textContent = st.hostname || '—';
    $('hostLabel').textContent = st.hostname ? st.hostname + ' · Linux' : 'GitHub Actions Runner';
    $('termTitle').textContent = 'runner@' + (st.hostname || 'vps') + ' — bash';
    if (st.ip) $('iIp').textContent = st.ip;
    else $('iIp').textContent = $('ipText').textContent;
    $('iTime').textContent = st.time || '—';

    setStatus(true);
}

// ---------------- Файлы ----------------
function joinPath(base, name) {
    return base === '.' ? name : base + '/' + name;
}

async function loadFiles() {
    const rows = $('fileRows');
    rows.innerHTML = '<tr><td colspan="5"><div class="empty-state"><div class="big">⏳</div>Загрузка…</div></td></tr>';
    renderCrumbs();

    const res = await api('files?path=' + encodeURIComponent(currentPath));
    if (res.error) {
        // Путь мог исчезнуть (папку удалили/переименовали) — поднимаемся к корню
        if (currentPath !== '.') {
            toast('Папка недоступна: ' + res.error, 'error');
            currentPath = '.';
            renderCrumbs();
            return loadFiles();
        }
        rows.innerHTML = `<tr><td colspan="5"><div class="empty-state"><div class="big">🚫</div>${esc(res.error)}</div></td></tr>`;
        return;
    }
    const files = res.files || [];
    if (!files.length) {
        rows.innerHTML = '<tr><td colspan="5"><div class="empty-state"><div class="big">📂</div>Папка пуста</div></td></tr>';
        return;
    }

    rows.innerHTML = files.map(f => {
        const full = joinPath(currentPath, f.name);
        const icon = fileIcon(f);
        const actions = f.is_dir
            ? `<button class="icon-btn del" title="Удалить папку" onclick="event.stopPropagation(); removeEntry('${escAttr(full)}')"><i class="fas fa-trash"></i></button>`
            : `<button class="icon-btn" title="Открыть / редактировать" onclick="event.stopPropagation(); editFile('${escAttr(full)}')"><i class="fas fa-pen"></i></button>
               <button class="icon-btn" title="Скачать" onclick="event.stopPropagation(); downloadFile('${escAttr(full)}')"><i class="fas fa-download"></i></button>
               <button class="icon-btn del" title="Удалить" onclick="event.stopPropagation(); removeEntry('${escAttr(full)}')"><i class="fas fa-trash"></i></button>`;
        const openFn = f.is_dir ? `openDir('${escAttr(f.name)}')` : `editFile('${escAttr(full)}')`;
        return `
        <tr class="${f.is_dir ? 'folder-row' : 'file-row'}" style="cursor:pointer"
            onclick="${openFn}" ondblclick="${openFn}" title="${f.is_dir ? 'Открыть папку' : 'Открыть файл'}">
            <td><div class="f-name"><span class="f-icon ${icon.cls}">${icon.html}</span>${esc(f.name)}</div></td>
            <td class="f-size">${f.is_dir ? 'папка' : fmtSize(f.size)}</td>
            <td class="f-date d-hide">${esc(f.modified)}</td>
            <td class="f-perms d-hide">${esc(f.permissions)}</td>
            <td><div class="f-actions">${actions}</div></td>
        </tr>`;
    }).join('');
}


function escAttr(s) {
    s = String(s == null ? '' : s);
    // Экранирование для одинарных кавычек внутри onclick="...('...')"
    return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'")
             .replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fileIcon(f) {
    if (f.is_dir) return { cls: 'dir', html: '<i class="fas fa-folder"></i>' };
    const n = f.name.toLowerCase();
    if (/\.(py|pyw)$/.test(n))      return { cls: 'py',  html: '<i class="fab fa-python"></i>' };
    if (/\.(sh|bash)$/.test(n))     return { cls: 'sh',  html: '<i class="fas fa-file-code"></i>' };
    if (/\.(png|jpe?g|gif|svg|webp|ico)$/.test(n)) return { cls: 'img', html: '<i class="fas fa-image"></i>' };
    if (/\.(zip|tar|gz|rar|7z|bz2)$/.test(n))      return { cls: 'zip', html: '<i class="fas fa-file-zipper"></i>' };
    if (/\.(js|json|ts|css|html|xml|yml|yaml|md|txt|conf|log)$/.test(n)) return { cls: 'doc', html: '<i class="fas fa-file-lines"></i>' };
    return { cls: '', html: '<i class="fas fa-file"></i>' };
}

function renderCrumbs() {
    const el = $('crumbs');
    let html = `<a onclick="gotoPath('.')"><i class="fas fa-house"></i> root</a>`;
    if (currentPath && currentPath !== '.') {
        const parts = currentPath.split('/');
        let acc = '';
        for (const p of parts) {
            acc = acc ? acc + '/' + p : p;
            html += ` <span>/</span> <a onclick="gotoPath('${escAttr(acc)}')">${esc(p)}</a>`;
        }
    }
    el.innerHTML = html;
}

function gotoPath(p) { currentPath = p; loadFiles(); }
function openDir(name) { currentPath = joinPath(currentPath, name); loadFiles(); }
function goUp() {
    if (currentPath === '.') return;
    const parts = currentPath.split('/');
    parts.pop();
    currentPath = parts.join('/') || '.';
    loadFiles();
}

function showNewModal(kind) {
    newKind = kind;
    $('newModalTitle').textContent = kind === 'dir' ? 'Новая папка' : 'Новый файл';
    $('newName').value = '';
    $('newModal').classList.add('open');
    setTimeout(() => $('newName').focus(), 50);
}

async function createEntry() {
    const name = $('newName').value.trim();
    if (!name) return;
    const endpoint = newKind === 'dir' ? 'mkdir' : 'touch';
    const res = await api(endpoint, { method: 'POST', body: { path: currentPath, name } });
    closeModal('newModal');
    if (res.error) toast('Ошибка: ' + res.error, 'error');
    else { toast(newKind === 'dir' ? 'Папка создана' : 'Файл создан', 'success'); loadFiles(); }
}

async function removeEntry(path) {
    if (!confirm('Удалить "' + path.split('/').pop() + '"? Это действие необратимо.')) return;
    const res = await api('delete', { method: 'POST', body: { path } });
    if (res.error) toast('Ошибка: ' + res.error, 'error');
    else { toast('Удалено', 'success'); loadFiles(); }
}

async function editFile(path) {
    const res = await api('read?path=' + encodeURIComponent(path));
    if (res.error) { toast('Ошибка: ' + res.error, 'error'); return; }
    editorPath = path;
    $('editorName').textContent = res.name;
    $('editorArea').value = res.content;
    $('editorArea').readOnly = !!res.binary;
    $('saveBtn').disabled = !!res.binary;
    $('editorModal').classList.add('open');
}

async function saveFile() {
    const res = await api('write', { method: 'POST', body: { path: editorPath, content: $('editorArea').value } });
    if (res.error) toast('Ошибка: ' + res.error, 'error');
    else { toast('Файл сохранён (закоммичен в Git)', 'success'); closeModal('editorModal'); loadFiles(); }
}

function downloadFile(path) {
    // скачивание через fetch + blob (токен в заголовке Authorization)
    fetch('/api/download?path=' + encodeURIComponent(path), {
        headers: { 'Authorization': 'Bearer ' + token }
    }).then(r => r.blob()).then(blob => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = path.split('/').pop();
        link.click();
        URL.revokeObjectURL(url);
    }).catch(() => toast('Ошибка скачивания', 'error'));
}

$('uploadInput') && document.addEventListener('change', async (e) => {
    if (e.target.id !== 'uploadInput') return;
    for (const file of e.target.files) {
        const buf = await file.arrayBuffer();
        let binary = '';
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        const b64 = btoa(binary);
        const res = await api('upload', { method: 'POST', body: { path: currentPath, name: file.name, content: b64 } });
        if (res.error) toast(file.name + ': ' + res.error, 'error');
        else toast(file.name + ' загружен', 'success');
    }
    e.target.value = '';
    loadFiles();
});

function closeModal(id) { $(id).classList.remove('open'); }

// ---------------- Терминал (стриминг через SSE) ----------------
function termLine(html, cls = '') {
    const out = $('termOut');
    const span = document.createElement('span');
    span.className = 'line ' + cls;
    span.innerHTML = html;
    out.appendChild(span);
    out.scrollTop = out.scrollHeight;
    return span;
}

function stripAnsi(s) {
    // PTY может присылать ANSI-последовательности — чистим их для читаемости
    return s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').replace(/\r/g, '');
}

function welcomeTerminal() {
    if ($('termOut').dataset.welcomed) return;
    $('termOut').dataset.welcomed = '1';
    termLine('<span class="dim">VPS Manager Terminal. Все команды выполняются на сервере-раннере.</span>');
    termLine('<span class="dim">Подсказка: ls, pwd, cat файл, top, df -h, free -h, python3 …</span>');
}

let currentES = null;

async function sendCommand() {
    const input = $('termIn');
    const cmd = input.value.trim();
    if (!cmd) return;
    input.value = '';
    termHistory.push(cmd);
    histIdx = termHistory.length;

    if (cmd === 'clear') { $('termOut').innerHTML = ''; $('termOut').dataset.welcomed = ''; return; }

    if (currentES) { try { currentES.close(); } catch (e) {} currentES = null; }

    termLine(`<span class="prompt">$</span> <span class="cmd">${esc(cmd)}</span>`);
    $('runBadge').classList.add('show');

    const res = await api('command', { method: 'POST', body: { command: cmd } });
    if (res.error) {
        termLine(esc(res.error), 'err');
        $('runBadge').classList.remove('show');
        return;
    }

    // Стримим вывод через EventSource (сервер отдаёт всю историю + новые данные)
    const es = new EventSource('/api/stream?id=' + encodeURIComponent(res.id) + '&auth=' + encodeURIComponent(token));
    currentES = es;
    const line = termLine('', 'out');
    let buffer = '';
    let exitCode = null;

    es.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'out') {
            buffer += stripAnsi(msg.text);
            line.textContent = buffer;
            $('termOut').scrollTop = $('termOut').scrollHeight;
        } else if (msg.type === 'err') {
            termLine(esc(stripAnsi(msg.text)), 'err');
        } else if (msg.type === 'exit') {
            exitCode = msg.text;
        } else if (msg.type === 'done') {
            es.close();
            if (currentES === es) currentES = null;
            $('runBadge').classList.remove('show');
            if (exitCode !== null && exitCode !== '0') {
                termLine(`процесс завершён, код выхода: ${exitCode}`, 'err');
            } else if (!buffer.trim()) {
                termLine('(нет вывода)', 'dim');
            }
        }
    };
    es.onerror = () => {
        es.close();
        if (currentES === es) currentES = null;
        $('runBadge').classList.remove('show');
    };
}

$('termIn') && document.addEventListener('keydown', (e) => {
    if (e.target.id !== 'termIn') return;
    if (e.key === 'Enter') { e.preventDefault(); sendCommand(); }
    if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (histIdx > 0) { histIdx--; e.target.value = termHistory[histIdx] || ''; }
    }
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (histIdx < termHistory.length - 1) { histIdx++; e.target.value = termHistory[histIdx] || ''; }
        else { histIdx = termHistory.length; e.target.value = ''; }
    }
});

// ---------------- Навигация ----------------
document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        $('page-' + btn.dataset.page).classList.add('active');
        if (btn.dataset.page === 'files') loadFiles();
        if (btn.dataset.page === 'terminal') $('termIn').focus();
    });
});

// ---------------- Кнопки ----------------
$('activateBtn').addEventListener('click', activate);
$('codeInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') activate(); });
$('ipChip').addEventListener('click', () => copyText($('ipText').textContent));
$('revokeBtn').addEventListener('click', async () => {
    if (!confirm('Отозвать доступ к серверу?')) return;
    await api('revoke', { method: 'POST', body: {} });
    toast('Доступ отозван', 'success');
    logout();
});

// ---------------- Инициализация ----------------
(async function init() {
    if (token) {
        const s = await api('session');
        if (s.trusted) {
            enterApp(s.ip, s.password);
            return;
        }
        localStorage.removeItem(TOKEN_KEY);
        token = '';
    }
    $('codeInput').focus();
})();
