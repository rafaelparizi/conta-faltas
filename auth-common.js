// Helpers de autenticação compartilhados por presente.html e admin.html.
// Requer que, ANTES deste script, já tenham sido carregados:
//   firebase-app-compat.js, firebase-auth-compat.js, firebase-config.js
// e chamado firebase.initializeApp(window.FIREBASE_CONFIG).

// Limpa a flag do login "fake" da v1 (stub). Se um navegador ainda tiver o
// auth.html antigo em cache, ele via essa flag e mandava direto pro
// presente.html — que, sem sessão Firebase, mandava de volta: loop.
try {
    localStorage.removeItem('presente_auth');
    localStorage.removeItem('presente_auth_ts');
} catch (_) { /* ignora */ }

// Espera o Firebase terminar de restaurar a sessão salva (IndexedDB) e
// devolve o usuário logado, ou null. Usa authStateReady() — o primeiro
// disparo de onAuthStateChanged pode vir null ANTES da sessão persistida
// ser lida, o que fazia o presente.html achar que ninguém estava logado
// logo depois do login no auth.html (e mandar de volta pro login em loop).
// ===== Datas "AAAA-MM-DD" (vigência de portaria) =====
// Tratadas como texto/UTC de propósito: new Date('2026-09-01') é meia-noite
// UTC, que no Brasil vira 31/08 — formatar pelo fuso local voltaria um dia.

function formatarDataBR(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    return m ? `${m[3]}/${m[2]}/${m[1]}` : '';
}

// Término previsto de uma portaria de 24 meses: véspera do mesmo dia, 24
// meses depois (01/09/2026 → 31/08/2028; 29/02/2024 → 28/02/2026).
function fimPrevistoPortaria(inicioIso, meses = 24) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(inicioIso || '');
    if (!m) return '';
    const aniversario = Date.UTC(+m[1], +m[2] - 1 + meses, +m[3]);
    return new Date(aniversario - 86400000).toISOString().slice(0, 10);
}

function _authStateReady(auth) {
    // O SDK "compat" não expõe authStateReady() no wrapper — só no objeto
    // modular interno (_delegate). Fallback: primeiro onAuthStateChanged.
    if (typeof auth.authStateReady === 'function') return auth.authStateReady();
    if (auth._delegate && typeof auth._delegate.authStateReady === 'function') {
        return auth._delegate.authStateReady();
    }
    return new Promise((resolve) => {
        const cancelar = auth.onAuthStateChanged(() => { cancelar(); resolve(); });
    });
}

async function _aguardarUsuarioFirebase() {
    const auth = firebase.auth();
    const timeout = new Promise((resolve) => setTimeout(() => resolve('timeout'), 10000));
    const pronto = _authStateReady(auth).then(() => 'ok');
    const resultado = await Promise.race([pronto, timeout]);
    if (resultado === 'timeout') {
        // Sem isso, se o Firebase não conseguir resolver o estado de login
        // (projeto mal configurado, rede bloqueando o Firebase), a tela
        // ficaria com o spinner pra sempre.
        console.error('Timeout esperando o Firebase confirmar o estado de login (10s). ' +
            'Verifique firebase-config.js e a conexão com o Firebase.');
        return null;
    }
    return auth.currentUser;
}

// Proteção contra loop de redirecionamento: se a página tentar redirecionar
// mais de N vezes num intervalo curto (ex.: auth.html <-> presente.html se
// a config do Firebase estiver errada ou a API estiver fora do ar), para de
// redirecionar e mostra um erro em vez de travar a aba num loop infinito.
function _podeRedirecionar() {
    const agora = Date.now();
    let historico = [];
    try { historico = JSON.parse(sessionStorage.getItem('_auth_redirects') || '[]'); } catch (_) { /* ignora */ }
    historico = historico.filter((t) => agora - t < 8000);
    historico.push(agora);
    try { sessionStorage.setItem('_auth_redirects', JSON.stringify(historico)); } catch (_) { /* ignora */ }
    return historico.length <= 5;
}

function redirecionarComProtecao(destino) {
    if (!_podeRedirecionar()) {
        document.documentElement.style.visibility = 'visible';
        console.error(
            'Loop de redirecionamento de login detectado (auth.html <-> presente.html/admin.html). ' +
            'Provável causa: firebase-config.js com valores inválidos/placeholder, ou a API fora do ar. ' +
            'Redirecionamento interrompido para não travar a aba.'
        );
        const aviso = document.createElement('div');
        aviso.style.cssText = 'position:fixed;inset:0;z-index:99999;background:#fff;display:flex;' +
            'align-items:center;justify-content:center;padding:24px;font-family:sans-serif;text-align:center;';
        aviso.innerHTML = '<div style="max-width:420px"><h1 style="font-size:1.1rem;font-weight:800;margin-bottom:8px;">' +
            'Não foi possível verificar seu login</h1><p style="font-size:0.9rem;color:#475569;">' +
            'Detectamos um loop entre as telas de login. Verifique se firebase-config.js está preenchido ' +
            'com os dados do projeto Firebase e se a API está no ar, depois recarregue a página.</p></div>';
        document.body.appendChild(aviso);
        return;
    }
    location.replace(destino);
}

// Espera o Firebase carregar o estado de login; se não houver ninguém
// logado, manda pra tela de acesso. Devolve o objeto User ou null.
async function exigirLogin(redirectPara = 'auth.html') {
    const user = await _aguardarUsuarioFirebase();
    if (!user) {
        redirecionarComProtecao(redirectPara);
        return null;
    }
    return user;
}

async function tokenAtual() {
    const user = firebase.auth().currentUser;
    if (!user) throw new Error('Usuário não está logado.');
    return user.getIdToken();
}

// fetch() que já anexa o Authorization: Bearer <token do Firebase>.
async function apiFetch(url, options = {}) {
    const token = await tokenAtual();
    const headers = Object.assign({}, options.headers, { Authorization: 'Bearer ' + token });
    return fetch(url, Object.assign({}, options, { headers }));
}

async function sairDaConta(redirectPara = 'auth.html') {
    try { await firebase.auth().signOut(); } catch (_) { /* ignora */ }
    location.replace(redirectPara);
}
