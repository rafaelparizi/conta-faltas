import os
import re
import ssl
import html
import base64
import json
import smtplib
import functools
import unicodedata
import tempfile
from email.message import EmailMessage
from email.utils import formataddr
from datetime import date, datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import pdfplumber
from flask import Flask, request, jsonify
from flask.json.provider import DefaultJSONProvider
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Datas/horas: o Firestore guarda em UTC; tudo que a API devolve sai no fuso
# de São Paulo, em ISO 8601 com offset (ex.: "2026-09-26T11:40:25-03:00").
# Sem isso o Flask mandava "Sat, 26 Sep 2026 14:40:25 GMT".
FUSO_SP = ZoneInfo("America/Sao_Paulo")


class _JSONProviderSP(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, datetime):
            if o.tzinfo is None:
                o = o.replace(tzinfo=ZoneInfo("UTC"))
            return o.astimezone(FUSO_SP).isoformat(timespec="seconds")
        return super().default(o)


app.json = _JSONProviderSP(app)


# =========================================================
# AUTENTICAÇÃO (Firebase) — login com Google + aprovação de coordenador
# =========================================================
#
# Fluxo: o frontend loga com Google via Firebase Authentication (client-side)
# e manda o ID token no header "Authorization: Bearer <token>" em toda
# chamada. Aqui a gente só VALIDA esse token (assinatura + expiração, via
# Firebase Admin SDK) e decide se o e-mail pode usar a API:
#   - e-mail em ADMIN_EMAILS → sempre aprovado (bootstrap, não depende do
#     Firestore existir/ter dado);
#   - senão, consulta o Firestore (coleção "coordenadores", doc = e-mail):
#     libera só status "aprovado" DENTRO da vigência da portaria confirmada
#     pelo admin; pendente / recusado / revogado / vigência vencida
#     bloqueiam (ver _situacao_acesso).
#
# Todo acesso ao Firestore (leitura E escrita) passa por aqui, usando o
# Admin SDK — o frontend nunca fala com o Firestore diretamente, só com o
# Firebase Auth (login) e com esta API. Isso evita ter que acertar regras de
# segurança do Firestore para o cliente: por padrão elas negam tudo.

ADMIN_EMAILS = {"rafael.parizi@iffarroupilha.edu.br"}

try:
    import firebase_admin
    from firebase_admin import credentials as fb_credentials
    from firebase_admin import auth as fb_auth
    from firebase_admin import firestore as fb_firestore

    if not firebase_admin._apps:
        _cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        _cred_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if _cred_path:
            firebase_admin.initialize_app(fb_credentials.Certificate(_cred_path))
        elif _cred_json:
            firebase_admin.initialize_app(fb_credentials.Certificate(json.loads(_cred_json)))
        # sem nenhuma das duas: fica sem inicializar (ex.: ambiente de dev
        # sem Firebase configurado ainda) — as rotas que dependem disso
        # devolvem erro claro em vez de derrubar a API inteira.

    _db = fb_firestore.client() if firebase_admin._apps else None
except Exception as _e:
    firebase_admin = None
    fb_auth = None
    _db = None
    print(f"Firebase Admin SDK não inicializado: {_e}")


class ErroAuth(Exception):
    def __init__(self, mensagem, status=401):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def _token_do_header():
    cabecalho = request.headers.get("Authorization", "")
    if not cabecalho.startswith("Bearer "):
        raise ErroAuth("Faça login para continuar.", 401)
    return cabecalho[len("Bearer "):].strip()


def _verificar_login():
    """Valida o ID token do Firebase e devolve o e-mail do usuário logado."""
    if fb_auth is None:
        raise ErroAuth("Login com Google ainda não está configurado nesta API.", 500)
    token = _token_do_header()
    try:
        decodificado = fb_auth.verify_id_token(token)
    except Exception:
        raise ErroAuth("Sessão inválida ou expirada. Faça login novamente.", 401)
    email = (decodificado.get("email") or "").lower().strip()
    if not email:
        raise ErroAuth("Conta Google sem e-mail associado.", 401)
    return email


def _parse_data(valor):
    """'AAAA-MM-DD' → date, ou None se vazio/inválido."""
    s = str(valor or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _fmt_data_br(valor):
    d = _parse_data(valor)
    return d.strftime("%d/%m/%Y") if d else ""


def _hoje_sp():
    return datetime.now(FUSO_SP).date()


def _validar_periodo(inicio_raw, fim_raw, rotulo):
    """Devolve (inicio, fim) como date, ou levanta ValueError com mensagem."""
    inicio, fim = _parse_data(inicio_raw), _parse_data(fim_raw)
    if not inicio or not fim:
        raise ValueError(f"Informe as datas de início e término da {rotulo} (formato AAAA-MM-DD).")
    if fim <= inicio:
        raise ValueError(f"A data de término da {rotulo} deve ser posterior à de início.")
    return inicio, fim


def _situacao_acesso(email):
    """Situação de acesso do e-mail: dict com 'status' e, conforme o caso,
    'vigencia_fim' e 'motivo'. Status: aprovado / pendente / revogado /
    expirado / rejeitado / None (nunca pediu).

    Ordem: aprovado dentro da vigência > pedido pendente (inclui quem foi
    revogado ou venceu e já mandou portaria nova) > revogado > vigência
    vencida > recusado. A vigência vale até o fim do dia de término, no
    horário de São Paulo; depois disso o acesso é bloqueado sozinho."""
    if email in ADMIN_EMAILS:
        return {"status": "aprovado"}
    if _db is None:
        return {"status": None}

    snap = _db.collection("coordenadores").document(email).get()
    coord = (snap.to_dict() or {}) if snap.exists else None
    aprovado = coord is not None and coord.get("status", "aprovado") == "aprovado"
    if aprovado:
        fim = _parse_data(coord.get("vigencia_fim"))
        if fim is None or _hoje_sp() <= fim:
            return {"status": "aprovado", "vigencia_fim": coord.get("vigencia_fim"),
                    "modalidade": coord.get("modalidade")}

    snap_sol = _db.collection("solicitacoes").document(email).get()
    sol = (snap_sol.to_dict() or {}) if snap_sol.exists else None
    if sol and sol.get("status") == "pendente":
        return {"status": "pendente"}
    if coord and coord.get("status") == "revogado":
        return {"status": "revogado", "motivo": coord.get("motivo_revogacao", "")}
    if aprovado:  # chegou aqui = vigência vencida
        return {"status": "expirado", "vigencia_fim": coord.get("vigencia_fim")}
    if sol and sol.get("status") == "rejeitado":
        return {"status": "rejeitado"}
    return {"status": None}


def _status_coordenador(email):
    return _situacao_acesso(email)["status"]


def _mensagem_bloqueio(sit):
    status = sit["status"]
    if status == "pendente":
        return "Seu acesso ainda está pendente de aprovação."
    if status == "rejeitado":
        return "Seu pedido de acesso foi recusado."
    if status == "revogado":
        motivo = sit.get("motivo") or "não informado"
        return f"Seu acesso foi revogado. Motivo: {motivo}"
    if status == "expirado":
        return (f"A vigência da sua portaria terminou em {_fmt_data_br(sit.get('vigencia_fim'))}. "
                "Envie a nova portaria para renovar o acesso.")
    return "Você ainda não solicitou acesso como coordenador."


def _nivel_da_requisicao(email, sit):
    """Nível de ensino em que a requisição opera. Coordenador: a modalidade
    aprovada (fixa). Admin: o nível escolhido no seletor da tela, enviado no
    header X-Nivel. Sem informação válida → NIVEL_PADRAO."""
    if email in ADMIN_EMAILS:
        escolhido = (request.headers.get("X-Nivel") or "").strip()
        return escolhido if escolhido in NIVEIS else NIVEL_PADRAO
    modalidade = sit.get("modalidade")
    return modalidade if modalidade in NIVEIS else NIVEL_PADRAO


def exige_aprovado(view):
    """Decorator: exige token Firebase válido + coordenador aprovado e dentro
    da vigência. Deixa passar OPTIONS (preflight de CORS) sem checar nada."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return "", 200
        try:
            email = _verificar_login()
            sit = _situacao_acesso(email)
            if sit["status"] != "aprovado":
                return jsonify({"erro": _mensagem_bloqueio(sit),
                                "status_acesso": sit["status"] or "sem_solicitacao"}), 403
        except ErroAuth as e:
            return jsonify({"erro": e.mensagem}), e.status
        request.email_usuario = email
        request.nivel = _nivel_da_requisicao(email, sit)
        return view(*args, **kwargs)
    return wrapper


def exige_admin(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return "", 200
        try:
            email = _verificar_login()
        except ErroAuth as e:
            return jsonify({"erro": e.mensagem}), e.status
        if email not in ADMIN_EMAILS:
            return jsonify({"erro": "Acesso restrito ao administrador."}), 403
        request.email_usuario = email
        return view(*args, **kwargs)
    return wrapper


# =========================================================
# NOTIFICAÇÕES POR E-MAIL
# =========================================================
#
# Enviadas por SMTP com a conta do admin (SMTP_USER / SMTP_PASSWORD — no
# Gmail/Google Workspace, uma "senha de app"). Sem SMTP_USER configurado, o
# envio é pulado. Falha no envio NUNCA derruba a solicitação nem a decisão:
# só é registrada no log e devolvida como email_enviado=false (e, para o
# admin, o motivo em email_erro).

EMAIL_CONTATO = "rafael.parizi@iffarroupilha.edu.br"

# Modalidade do curso informada no pedido de acesso (chave gravada → rótulo).
MODALIDADES = {
    "tecnico_integrado": "Técnico Integrado",
    "tecnico_subsequente": "Técnico Subsequente",
    "superior": "Superior",
    "eja": "EJA",
    "pos_graduacao": "Pós-graduação",
}
NIVEL_PADRAO = "superior"
LINK_ACESSO = os.environ.get("APP_URL", "https://rafaelparizi.github.io/conta-faltas/auth.html")
LINK_ADMIN = os.environ.get("ADMIN_URL", LINK_ACESSO.rsplit("/", 1)[0] + "/admin.html")


def _decodificar_data_url(data_url):
    """'data:<mime>;base64,<dados>' → (bytes, mime), ou None se inválido."""
    m = re.match(r"data:([^;,]*)(;base64)?,(.*)$", data_url or "", re.S)
    if not m or not m.group(2):
        return None
    try:
        return base64.b64decode(m.group(3)), (m.group(1) or "application/octet-stream")
    except Exception:
        return None


def _enviar_email(para, assunto, texto, html_corpo, reply_to=None, anexos=None):
    """Devolve (enviado: bool, motivo_da_falha: str | None).
    anexos: lista de (bytes, mime, nome_do_arquivo)."""
    usuario = os.environ.get("SMTP_USER", "").strip()
    if not usuario:
        print(f"E-mail não enviado para {para} (SMTP_USER não configurado): {assunto}")
        return False, "SMTP_USER não configurado na API"
    senha = os.environ.get("SMTP_PASSWORD", "")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    porta = int(os.environ.get("SMTP_PORT", "587"))

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr(("Rafael Parizi · presente.edu", usuario))
    msg["To"] = para
    msg["Reply-To"] = reply_to or EMAIL_CONTATO
    msg.set_content(texto)
    msg.add_alternative(html_corpo, subtype="html")
    for dados, mime, nome in anexos or []:
        principal, _, sub = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(dados, maintype=principal, subtype=sub or "octet-stream", filename=nome)

    try:
        if porta == 465:
            servidor = smtplib.SMTP_SSL(host, porta, timeout=15, context=ssl.create_default_context())
        else:
            servidor = smtplib.SMTP(host, porta, timeout=15)
            servidor.ehlo()
            if servidor.has_extn("starttls"):
                servidor.starttls(context=ssl.create_default_context())
                servidor.ehlo()
        with servidor:
            if senha:
                servidor.login(usuario, senha)
            servidor.send_message(msg)
        return True, None
    except Exception as e:
        print(f"Falha ao enviar e-mail para {para} ({assunto}): {e}")
        return False, f"{type(e).__name__}: {e}"


def _html_email(paragrafos_html):
    corpo = "".join(f'<p style="margin:0 0 14px">{p}</p>' for p in paragrafos_html)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;'
        'color:#1e293b;max-width:560px">'
        f'{corpo}'
        '<p style="margin:24px 0 0;color:#64748b;font-size:13px">Rafael Parizi<br>'
        f'presente.edu · <a href="mailto:{EMAIL_CONTATO}" style="color:#32a041">{EMAIL_CONTATO}</a></p>'
        '</div>'
    )


def _assinatura_texto():
    return f"\n\nRafael Parizi\npresente.edu · {EMAIL_CONTATO}\n"


def _link(url):
    return f'<a href="{url}" style="color:#32a041">{url}</a>'


_CONTATO_HTML = f'<a href="mailto:{EMAIL_CONTATO}" style="color:#32a041">{EMAIL_CONTATO}</a>'


def notificar_solicitacao_recebida(email, nome, curso, inicio, fim):
    n, c = html.escape(nome), html.escape(curso)
    periodo = f"{_fmt_data_br(inicio)} a {_fmt_data_br(fim)}"
    texto = (
        f"Olá, {nome}.\n\n"
        f"Recebi sua solicitação de acesso ao presente.edu como coordenador(a) do curso {curso}, "
        f"com portaria de vigência de {periodo}. Ela está em análise, e você vai receber outro "
        "e-mail assim que ela for avaliada.\n\n"
        "Enquanto isso, se entrar no sistema, vai ver o aviso \"Solicitação em análise\".\n\n"
        f"Se tiver alguma dúvida, é só responder este e-mail ou escrever para {EMAIL_CONTATO}."
        + _assinatura_texto()
    )
    html_corpo = _html_email([
        f"Olá, {n}.",
        f"Recebi sua solicitação de acesso ao <strong>presente.edu</strong> como coordenador(a) do "
        f"curso <strong>{c}</strong>, com portaria de vigência de <strong>{periodo}</strong>. "
        "Ela está <strong>em análise</strong>, e você vai receber outro e-mail assim que ela for avaliada.",
        "Enquanto isso, se entrar no sistema, vai ver o aviso “Solicitação em análise”.",
        f"Se tiver alguma dúvida, é só responder este e-mail ou escrever para {_CONTATO_HTML}.",
    ])
    return _enviar_email(email, "presente.edu — solicitação de acesso recebida", texto, html_corpo)


def notificar_admin_nova_solicitacao(email, nome, curso, inicio, fim, comprovante, comprovante_nome):
    """Avisa os admins de um pedido novo, com a portaria anexada. Reply-To é
    quem pediu, para responder direto. Devolve o resultado do último envio."""
    periodo = f"{_fmt_data_br(inicio)} a {_fmt_data_br(fim)}"
    anexos = []
    decodificado = _decodificar_data_url(comprovante)
    if decodificado:
        dados, mime = decodificado
        extensao = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}.get(mime, "")
        anexos.append((dados, mime, comprovante_nome or f"portaria{extensao}"))
    aviso_anexo = ("A portaria está anexada a este e-mail." if anexos
                   else "Não foi possível anexar a portaria — veja no painel.")
    texto = (
        "Novo pedido de acesso ao presente.edu:\n\n"
        f"Nome: {nome}\nE-mail: {email}\nCurso: {curso}\n"
        f"Vigência da portaria (informada): {periodo}\n\n"
        f"{aviso_anexo}\n\n"
        f"Para aprovar ou recusar (e confirmar as datas): {LINK_ADMIN}\n"
        "Responder este e-mail responde direto para quem pediu."
    )
    html_corpo = _html_email([
        "Novo pedido de acesso ao <strong>presente.edu</strong>:",
        f"<strong>Nome:</strong> {html.escape(nome)}<br>"
        f"<strong>E-mail:</strong> {html.escape(email)}<br>"
        f"<strong>Curso:</strong> {html.escape(curso)}<br>"
        f"<strong>Vigência da portaria (informada):</strong> {periodo}",
        aviso_anexo,
        f"Para aprovar ou recusar (e confirmar as datas): {_link(LINK_ADMIN)}",
        "Responder este e-mail responde direto para quem pediu.",
    ])
    resultado = (False, "nenhum admin configurado")
    for admin in sorted(ADMIN_EMAILS):
        resultado = _enviar_email(admin, f"presente.edu — novo pedido de acesso: {nome}",
                                  texto, html_corpo, reply_to=email, anexos=anexos)
    return resultado


def notificar_acesso_aprovado(email, nome, vigencia_fim):
    n, e = html.escape(nome or ""), html.escape(email)
    saudacao = f"Olá, {nome}." if nome else "Olá."
    ate = _fmt_data_br(vigencia_fim)
    texto = (
        f"{saudacao}\n\n"
        "Sua solicitação de acesso ao presente.edu foi aprovada. Para entrar:\n\n"
        f"1. Acesse {LINK_ACESSO}\n"
        f"2. Clique em \"Entrar com Google\" e use esta mesma conta ({email}).\n"
        "3. Você vai direto para a ferramenta. Na barra lateral, envie os diários de classe "
        "(PDF exportado do SIGAA) e clique em \"Processar relatório\".\n\n"
        f"Seu acesso vale até {ate}, fim da vigência da sua portaria. Depois disso, é só entrar "
        "e enviar a nova portaria para renovar.\n\n"
        f"Qualquer dúvida, é só responder este e-mail ou escrever para {EMAIL_CONTATO}."
        + _assinatura_texto()
    )
    html_corpo = _html_email([
        f"Olá, {n}." if nome else "Olá.",
        "Sua solicitação de acesso ao <strong>presente.edu</strong> foi <strong>aprovada</strong>. Para entrar:",
        f'1. Acesse {_link(LINK_ACESSO)}<br>'
        f'2. Clique em <strong>“Entrar com Google”</strong> e use esta mesma conta ({e}).<br>'
        '3. Você vai direto para a ferramenta. Na barra lateral, envie os diários de classe '
        '(PDF exportado do SIGAA) e clique em <strong>“Processar relatório”</strong>.',
        f"Seu acesso vale até <strong>{ate}</strong>, fim da vigência da sua portaria. Depois disso, "
        "é só entrar e enviar a nova portaria para renovar.",
        f"Qualquer dúvida, é só responder este e-mail ou escrever para {_CONTATO_HTML}.",
    ])
    return _enviar_email(email, "presente.edu — acesso aprovado", texto, html_corpo)


def notificar_acesso_recusado(email, nome):
    n = html.escape(nome or "")
    saudacao = f"Olá, {nome}." if nome else "Olá."
    texto = (
        f"{saudacao}\n\n"
        "Sua solicitação de acesso ao presente.edu não foi aprovada.\n\n"
        "Se você acha que houve um engano, ou quer enviar outra portaria de designação como "
        f"coordenador(a), entre em contato comigo pelo e-mail {EMAIL_CONTATO} (ou simplesmente "
        "responda esta mensagem)."
        + _assinatura_texto()
    )
    html_corpo = _html_email([
        f"Olá, {n}." if nome else "Olá.",
        "Sua solicitação de acesso ao <strong>presente.edu</strong> não foi aprovada.",
        "Se você acha que houve um engano, ou quer enviar outra portaria de designação como "
        f"coordenador(a), entre em contato comigo pelo e-mail {_CONTATO_HTML} "
        "(ou simplesmente responda esta mensagem).",
    ])
    return _enviar_email(email, "presente.edu — solicitação de acesso não aprovada", texto, html_corpo)


def notificar_acesso_revogado(email, nome, motivo):
    n, m = html.escape(nome or ""), html.escape(motivo)
    saudacao = f"Olá, {nome}." if nome else "Olá."
    texto = (
        f"{saudacao}\n\n"
        "Seu acesso ao presente.edu foi revogado.\n\n"
        f"Motivo: {motivo}\n\n"
        f"Se você tem uma nova portaria de designação como coordenador(a), entre em {LINK_ACESSO} "
        "com sua conta Google e envie a portaria pelo formulário para pedir o acesso de novo.\n\n"
        f"Se tiver dúvidas, entre em contato comigo pelo e-mail {EMAIL_CONTATO} (ou simplesmente "
        "responda esta mensagem)."
        + _assinatura_texto()
    )
    html_corpo = _html_email([
        f"Olá, {n}." if nome else "Olá.",
        "Seu acesso ao <strong>presente.edu</strong> foi <strong>revogado</strong>.",
        f"<strong>Motivo:</strong> {m}",
        f"Se você tem uma nova portaria de designação como coordenador(a), entre em {_link(LINK_ACESSO)} "
        "com sua conta Google e envie a portaria pelo formulário para pedir o acesso de novo.",
        f"Se tiver dúvidas, entre em contato comigo pelo e-mail {_CONTATO_HTML} "
        "(ou simplesmente responda esta mensagem).",
    ])
    return _enviar_email(email, "presente.edu — acesso revogado", texto, html_corpo)


# =========================================================
# ROTAS DE ACESSO
# =========================================================

_ERRO_SEM_FIRESTORE = "Cadastro de acesso ainda não está configurado nesta API."


@app.route("/auth/status", methods=["GET", "OPTIONS"])
def auth_status():
    if request.method == "OPTIONS":
        return "", 200
    try:
        email = _verificar_login()
    except ErroAuth as e:
        return jsonify({"erro": e.mensagem}), e.status

    sit = _situacao_acesso(email)
    resposta = {"status": sit["status"] or "novo", "email": email}
    if sit["status"] == "aprovado":
        admin = email in ADMIN_EMAILS
        nivel = _nivel_da_requisicao(email, sit)
        resposta.update({"admin": admin, "nivel": nivel, "nivel_rotulo": rotulo_nivel(nivel),
                         "recursos": recursos_do_nivel(nivel)})
        if admin:  # opções do seletor de nível do admin
            resposta["niveis"] = [{"valor": n, "rotulo": rotulo_nivel(n), **recursos_do_nivel(n)}
                                  for n in NIVEIS]
    if sit.get("vigencia_fim"):
        resposta["vigencia_fim"] = sit["vigencia_fim"]
    if sit["status"] == "revogado":
        resposta["motivo"] = sit.get("motivo", "")
    return jsonify(resposta)


@app.route("/auth/solicitar", methods=["POST", "OPTIONS"])
def auth_solicitar():
    if request.method == "OPTIONS":
        return "", 200
    try:
        email = _verificar_login()
    except ErroAuth as e:
        return jsonify({"erro": e.mensagem}), e.status
    if _db is None:
        return jsonify({"erro": _ERRO_SEM_FIRESTORE}), 500

    if _status_coordenador(email) == "aprovado":
        return jsonify({"status": "aprovado", "email": email})

    dados = request.get_json(silent=True) or {}
    nome = (dados.get("nome") or "").strip()
    curso = (dados.get("curso") or "").strip()
    modalidade = (dados.get("modalidade") or "").strip()
    comprovante_base64 = dados.get("comprovante_base64") or ""
    if modalidade not in MODALIDADES:
        return jsonify({"erro": "Informe a modalidade do curso (técnico integrado, subsequente, superior, EJA ou pós-graduação)."}), 400
    if not nome or not curso or not comprovante_base64:
        return jsonify({"erro": "Preencha nome, curso e anexe a portaria."}), 400
    try:
        inicio, fim = _validar_periodo(dados.get("portaria_inicio"), dados.get("portaria_fim"), "portaria")
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
    # ~700KB de arquivo vira ~950KB em base64 — folga do limite de 1MiB/doc do Firestore.
    if len(comprovante_base64) > 1_000_000:
        return jsonify({"erro": "Portaria muito grande (máx. ~700KB). Reduza o arquivo e tente novamente."}), 400

    comprovante_nome = (dados.get("comprovante_nome") or "")[:200]
    _db.collection("solicitacoes").document(email).set({
        "email": email,
        "nome": nome,
        "curso": curso,
        "modalidade": modalidade,
        "portaria_inicio": inicio.isoformat(),
        "portaria_fim": fim.isoformat(),
        "comprovante_base64": comprovante_base64,
        "comprovante_nome": comprovante_nome,
        "status": "pendente",
        "criado_em": fb_firestore.SERVER_TIMESTAMP,
    })
    curso_desc = f"{curso} ({MODALIDADES[modalidade]})"
    enviado, _ = notificar_solicitacao_recebida(email, nome, curso_desc, inicio.isoformat(), fim.isoformat())
    notificar_admin_nova_solicitacao(email, nome, curso_desc, inicio.isoformat(), fim.isoformat(),
                                     comprovante_base64, comprovante_nome)
    return jsonify({"status": "pendente", "email": email, "email_enviado": enviado})


@app.route("/admin/pendentes", methods=["GET", "OPTIONS"])
@exige_admin
def admin_pendentes():
    if _db is None:
        return jsonify({"erro": _ERRO_SEM_FIRESTORE}), 500
    docs = _db.collection("solicitacoes").where("status", "==", "pendente").stream()
    solicitacoes = []
    for d in docs:
        item = d.to_dict() or {}
        item["tem_comprovante"] = bool(item.pop("comprovante_base64", None))  # não manda o base64 na listagem
        solicitacoes.append(item)
    return jsonify({"solicitacoes": solicitacoes})


@app.route("/admin/comprovante/<email>", methods=["GET", "OPTIONS"])
@exige_admin
def admin_comprovante(email):
    if _db is None:
        return jsonify({"erro": _ERRO_SEM_FIRESTORE}), 500
    doc = _db.collection("solicitacoes").document(email.lower().strip()).get()
    if not doc.exists:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    d = doc.to_dict() or {}
    return jsonify({
        "comprovante_base64": d.get("comprovante_base64", ""),
        "comprovante_nome": d.get("comprovante_nome", ""),
    })


@app.route("/admin/decidir", methods=["POST", "OPTIONS"])
@exige_admin
def admin_decidir():
    if _db is None:
        return jsonify({"erro": _ERRO_SEM_FIRESTORE}), 500
    dados = request.get_json(silent=True) or {}
    email = (dados.get("email") or "").lower().strip()
    decisao = dados.get("decisao")
    if not email or decisao not in ("aprovar", "rejeitar"):
        return jsonify({"erro": "Informe email e decisao ('aprovar' ou 'rejeitar')."}), 400

    sol_ref = _db.collection("solicitacoes").document(email)
    sol = sol_ref.get()
    if not sol.exists:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    dados_sol = sol.to_dict() or {}

    if decisao == "aprovar":
        # Datas CONFIRMADAS pelo admin (pode ter ajustado as informadas).
        try:
            inicio, fim = _validar_periodo(dados.get("vigencia_inicio"), dados.get("vigencia_fim"), "vigência")
        except ValueError as e:
            return jsonify({"erro": str(e)}), 400
        _db.collection("coordenadores").document(email).set({
            "email": email,
            "nome": dados_sol.get("nome", ""),
            "curso": dados_sol.get("curso", ""),
            "modalidade": dados_sol.get("modalidade", ""),
            "status": "aprovado",
            "vigencia_inicio": inicio.isoformat(),
            "vigencia_fim": fim.isoformat(),
            "aprovado_por": request.email_usuario,
            "aprovado_em": fb_firestore.SERVER_TIMESTAMP,
        })
        sol_ref.update({"status": "aprovado"})
        enviado, erro_email = notificar_acesso_aprovado(email, dados_sol.get("nome", ""), fim.isoformat())
    else:
        sol_ref.update({"status": "rejeitado"})
        enviado, erro_email = notificar_acesso_recusado(email, dados_sol.get("nome", ""))

    return jsonify({"status": "ok", "email": email, "decisao": decisao,
                    "email_enviado": enviado, "email_erro": erro_email})


@app.route("/admin/coordenadores", methods=["GET", "OPTIONS"])
@exige_admin
def admin_coordenadores():
    """Coordenadores com status aprovado, com a situação da vigência:
    vigente / vence_em_breve (≤ 30 dias) / vencida."""
    if _db is None:
        return jsonify({"erro": _ERRO_SEM_FIRESTORE}), 500
    hoje = _hoje_sp()
    lista = []
    for d in _db.collection("coordenadores").where("status", "==", "aprovado").stream():
        c = d.to_dict() or {}
        fim = _parse_data(c.get("vigencia_fim"))
        dias = (fim - hoje).days if fim else None
        situacao = ("vigente" if dias is None or dias > 30
                    else "vence_em_breve" if dias >= 0
                    else "vencida")
        lista.append({
            "email": c.get("email", d.id), "nome": c.get("nome", ""), "curso": c.get("curso", ""),
            "modalidade": c.get("modalidade", ""),
            "vigencia_inicio": c.get("vigencia_inicio"), "vigencia_fim": c.get("vigencia_fim"),
            "dias_restantes": dias, "situacao": situacao,
        })
    lista.sort(key=lambda c: (c["vigencia_fim"] is None, c["vigencia_fim"] or "", c["nome"]))
    return jsonify({"coordenadores": lista})


@app.route("/admin/revogar", methods=["POST", "OPTIONS"])
@exige_admin
def admin_revogar():
    if _db is None:
        return jsonify({"erro": _ERRO_SEM_FIRESTORE}), 500
    dados = request.get_json(silent=True) or {}
    email = (dados.get("email") or "").lower().strip()
    motivo = (dados.get("motivo") or "").strip()[:500]
    if not email or not motivo:
        return jsonify({"erro": "Informe o e-mail e o motivo da revogação."}), 400

    ref = _db.collection("coordenadores").document(email)
    snap = ref.get()
    if not snap.exists or (snap.to_dict() or {}).get("status") != "aprovado":
        return jsonify({"erro": "Coordenador com acesso ativo não encontrado."}), 404

    ref.update({
        "status": "revogado",
        "motivo_revogacao": motivo,
        "revogado_por": request.email_usuario,
        "revogado_em": fb_firestore.SERVER_TIMESTAMP,
    })
    enviado, erro_email = notificar_acesso_revogado(email, (snap.to_dict() or {}).get("nome", ""), motivo)
    return jsonify({"status": "ok", "email": email, "email_enviado": enviado, "email_erro": erro_email})


# =========================================================
# PERFIL DA INSTITUIÇÃO (por usuário)
# =========================================================
#
# Dados da instituição de quem usa a ferramenta: instituição, campus, sigla,
# logo e endereço. Salvar parcial é permitido (dá pra ir preenchendo); o
# perfil conta como "completo" quando todos os obrigatórios estão preenchidos.

UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB",
       "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}
# campo → (rótulo, obrigatório, tamanho máximo)
CAMPOS_PERFIL = {
    "instituicao": ("Instituição", True, 200),
    "campus": ("Campus", True, 120),
    "sigla": ("Sigla", True, 20),
    "cep": ("CEP", True, 9),
    "logradouro": ("Logradouro", True, 200),
    "numero": ("Número", True, 20),
    "complemento": ("Complemento", False, 120),
    "bairro": ("Bairro", True, 120),
    "cidade": ("Cidade", True, 120),
    "uf": ("UF", True, 2),
}
LOGO_MIMES = ("image/png", "image/jpeg", "image/webp")  # sem SVG: pode carregar script
LOGO_MAX_BASE64 = 450_000  # ~300KB de imagem


def _faltando_no_perfil(perfil):
    faltando = [rotulo for campo, (rotulo, obrig, _) in CAMPOS_PERFIL.items()
                if obrig and not (perfil.get(campo) or "").strip()]
    if not perfil.get("logo_base64"):
        faltando.append("Logo")
    return faltando


def _resposta_perfil(perfil):
    faltando = _faltando_no_perfil(perfil)
    return {"perfil": perfil, "completo": not faltando, "faltando": faltando}


def _dados_usuario(email):
    """Dados do próprio usuário para a aba "Meus dados" (somente leitura):
    vêm do registro de coordenador aprovado. Admin pode não ter registro."""
    snap = _db.collection("coordenadores").document(email).get()
    c = (snap.to_dict() or {}) if snap.exists else {}
    return {
        "email": email,
        "admin": email in ADMIN_EMAILS,
        "nome": c.get("nome", ""),
        "curso": c.get("curso", ""),
        "modalidade": c.get("modalidade", ""),
        "vigencia_inicio": c.get("vigencia_inicio"),
        "vigencia_fim": c.get("vigencia_fim"),
    }


@app.route("/perfil", methods=["GET", "PUT", "OPTIONS"])
@exige_aprovado
def perfil():
    if _db is None:
        return jsonify({"erro": _ERRO_SEM_FIRESTORE}), 500
    ref = _db.collection("perfis").document(request.email_usuario)

    if request.method == "GET":
        snap = ref.get()
        dados = (snap.to_dict() or {}) if snap.exists else {}
        dados.pop("atualizado_em", None)
        dados.pop("email", None)
        return jsonify({**_resposta_perfil(dados), "usuario": _dados_usuario(request.email_usuario)})

    entrada = request.get_json(silent=True) or {}
    novo_perfil = {}
    for campo, (rotulo, _, maximo) in CAMPOS_PERFIL.items():
        valor = str(entrada.get(campo) or "").strip()
        if len(valor) > maximo:
            return jsonify({"erro": f"{rotulo}: máximo de {maximo} caracteres."}), 400
        novo_perfil[campo] = valor

    novo_perfil["uf"] = novo_perfil["uf"].upper()
    if novo_perfil["uf"] and novo_perfil["uf"] not in UFS:
        return jsonify({"erro": "UF inválida."}), 400
    cep_digitos = re.sub(r"\D", "", novo_perfil["cep"])
    if novo_perfil["cep"] and len(cep_digitos) != 8:
        return jsonify({"erro": "CEP inválido (use 8 dígitos, ex.: 97670-000)."}), 400
    if cep_digitos:
        novo_perfil["cep"] = f"{cep_digitos[:5]}-{cep_digitos[5:]}"

    logo = entrada.get("logo_base64") or ""
    if logo:
        if not any(logo.startswith(f"data:{m};base64,") for m in LOGO_MIMES):
            return jsonify({"erro": "Logo: envie uma imagem PNG, JPG ou WEBP."}), 400
        if len(logo) > LOGO_MAX_BASE64:
            return jsonify({"erro": "Logo muito grande (máx. ~300KB). Reduza a imagem e tente novamente."}), 400
    novo_perfil["logo_base64"] = logo

    ref.set({**novo_perfil, "email": request.email_usuario, "atualizado_em": fb_firestore.SERVER_TIMESTAMP})
    return jsonify(_resposta_perfil(novo_perfil))


# =========================================================
# FUNÇÕES AUXILIARES GERAIS
# =========================================================

def sem_acento(s):
    """Remove acentos de uma string para comparação normalizada."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def normalizar_texto_mes(texto):
    """
    Normaliza texto de mês que pode ter sido extraído verticalmente pelo pdfplumber.
    Ex: 'F\\ne\\nv\\ne\\nr\\ne\\ni\\nr\\no' → 'fevereiro'
    """
    MESES_VALIDOS = [
        "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"
    ]
    MESES_CANONICOS = {
        "janeiro": "janeiro", "fevereiro": "fevereiro", "marco": "março",
        "abril": "abril", "maio": "maio", "junho": "junho",
        "julho": "julho", "agosto": "agosto", "setembro": "setembro",
        "outubro": "outubro", "novembro": "novembro", "dezembro": "dezembro"
    }

    texto_limpo = re.sub(r"[\s\n\r]+", "", texto).lower()
    texto_sem_acento = sem_acento(texto_limpo)

    for mes in MESES_VALIDOS:
        if mes in texto_sem_acento or texto_sem_acento in mes:
            return MESES_CANONICOS[mes]

    return texto_limpo  # fallback


def extrair_valor_rotulo_multilinha(linhas, rotulo, proximos_rotulos=None):
    if proximos_rotulos is None:
        proximos_rotulos = []

    for i, linha in enumerate(linhas):
        if linha.startswith(rotulo):
            valor_inicial = linha.split(rotulo, 1)[1].strip()
            partes = [valor_inicial] if valor_inicial else []

            j = i + 1
            while j < len(linhas):
                prox = linhas[j].strip()
                if not prox or any(prox.startswith(r) for r in proximos_rotulos) or prox.endswith(":"):
                    break
                partes.append(prox)
                j += 1

            return " ".join([p for p in partes if p]).strip()
    return ""


# =========================================================
# EXTRAÇÃO DE METADADOS
# =========================================================

def extrair_metadados_pdf(caminho_pdf):
    metadados = {
        "Centro": "", "Curso": "", "Coordenador do Curso": "",
        "Código": "", "Disciplina": "", "Carga Horária": "",
        "Ano/Semestre": "", "Docente": "", "Matrícula Docente": ""
    }

    with pdfplumber.open(caminho_pdf) as pdf:
        # --- PÁGINA 1: dados gerais ---
        if len(pdf.pages) >= 1:
            texto_p1 = pdf.pages[0].extract_text() or ""
            linhas_p1 = [l.strip() for l in texto_p1.split("\n") if l.strip()]

            rotulos_p1 = [
                "Centro:", "Curso:", "Coordenador de Curso:",
                "Coordenador do Curso:", "Código:", "Disciplina:",
                "Créditos:", "Carga Horária:", "Turma:", "Ano/Semestre:"
            ]

            metadados["Centro"] = extrair_valor_rotulo_multilinha(linhas_p1, "Centro:", rotulos_p1)
            curso_bruto = extrair_valor_rotulo_multilinha(
            linhas_p1, "Curso:", ["Coordenador de Curso:", "Coordenador do Curso:", "Código:"]
            )

            curso_bruto = re.split(r"Coordenador de Curso:|Coordenador do Curso:|Coordenador de|Código:",
            curso_bruto,flags=re.IGNORECASE)[0].strip()

            metadados["Curso"] = curso_bruto

            coordenador = extrair_valor_rotulo_multilinha(
                linhas_p1, "Coordenador de Curso:", rotulos_p1
            )

            if not coordenador:
                coordenador = extrair_valor_rotulo_multilinha(
                    linhas_p1, "Coordenador do Curso:", rotulos_p1
                )

            # trata o caso em que o PDF quebra o rótulo:
            # "Coordenador de"
            # "RAFAEL BALDIATI PARIZI"
            # "Curso:"
            if not coordenador:
                for i in range(len(linhas_p1) - 2):
                    linha_atual = linhas_p1[i].strip().lower()
                    linha_meio = linhas_p1[i + 1].strip()
                    proxima_linha = linhas_p1[i + 2].strip().lower()

                    if linha_atual == "coordenador de" and proxima_linha.startswith("curso:"):
                        coordenador = linha_meio.strip()
                        break

            metadados["Coordenador do Curso"] = coordenador

            # "Código:" e "Disciplina:" — quando o nome da disciplina é longo,
            # o PDF quebra o texto e a 1ª linha do nome "vaza" para a linha do
            # código (ex.: "Código: 08023270" seguido de "ACESSIBILIDADE E
            # INCLUSÃO NO SISTEMA DE" e só depois "Disciplina:" / "INFORMAÇÃO").
            # Separa o código só-numérico do restante e devolve esse pedaço
            # para o nome da disciplina.
            codigo_bruto = extrair_valor_rotulo_multilinha(linhas_p1, "Código:", rotulos_p1)
            partes_cod = codigo_bruto.split(None, 1)
            if partes_cod and any(c.isdigit() for c in partes_cod[0]):
                metadados["Código"] = partes_cod[0]
                nome_prefixo = partes_cod[1].strip() if len(partes_cod) > 1 else ""
            else:
                metadados["Código"] = codigo_bruto.strip()
                nome_prefixo = ""

            disciplina_bruta = extrair_valor_rotulo_multilinha(
                linhas_p1, "Disciplina:", ["Créditos:", "Carga Horária:", "Código:"]
            )
            disciplina_bruta = disciplina_bruta.split("Créditos:")[0].strip()
            metadados["Disciplina"] = (nome_prefixo + " " + disciplina_bruta).strip()
            metadados["Carga Horária"] = extrair_valor_rotulo_multilinha(
                linhas_p1, "Carga Horária:", rotulos_p1
            )

            for linha in linhas_p1:
                m = re.search(r"(\d{4}\.\d)", linha)
                if m:
                    metadados["Ano/Semestre"] = m.group(1)
                    break

        # --- PÁGINAS 1 e 2: busca de docente ---
        for pagina in pdf.pages[:2]:
            if metadados["Docente"] and metadados["Matrícula Docente"]:
                break

            texto = pagina.extract_text() or ""
            linhas = [l.strip() for l in texto.split("\n") if l.strip()]

            for i, linha in enumerate(linhas):
                if "Docente" in linha:
                    valor = re.split(r"Docente\(s\)|Docente:", linha, flags=re.IGNORECASE)[-1].strip()
                    if not valor and (i + 1) < len(linhas):
                        valor = linhas[i + 1].strip()
                    if valor:
                        metadados["Docente"] = re.split(r"\s-\s\d+h", valor)[0].strip()

                if "Matrícula" in linha and not metadados["Matrícula Docente"]:
                    valor_m = linha.replace("Matrícula", "").strip()
                    if not valor_m and (i + 1) < len(linhas):
                        valor_m = linhas[i + 1].strip()
                    m = re.search(r"(\d{5,})", valor_m)
                    if m:
                        metadados["Matrícula Docente"] = m.group(1)

    return metadados


def _ch_int(carga_horaria_str):
    try:
        return int(str(carga_horaria_str).strip())
    except (ValueError, TypeError):
        return 0


def inferir_peso_disciplina(carga_horaria_str, pesos_por_codigo=None, codigo=None, nivel=None):
    """
    Determina o número de períodos por dia de aula da disciplina.

    Regras:
      - Se o frontend enviou um peso explícito para este código → usa ele
      - Senão, a tabela CH → períodos do NÍVEL (ver NIVEIS)
      - CH não reconhecida → períodos padrão do nível
    """
    if pesos_por_codigo and codigo and codigo in pesos_por_codigo:
        try:
            return int(pesos_por_codigo[codigo])
        except (ValueError, TypeError):
            pass

    return periodos_sugeridos(nivel, carga_horaria_str) or config_nivel(nivel)["periodos_padrao"]


# =========================================================
# FUNÇÕES DE TABELA DE FREQUÊNCIA
# =========================================================

def encontrar_tabela_frequencia(pdf):
    for pagina in pdf.pages:
        texto = pagina.extract_text() or ""
        if "Lista de Freq" in texto or "Lista de Frequ" in texto:
            tabela = pagina.extract_table()
            if tabela and len(tabela) > 2:
                return tabela
    return None


def mapear_colunas_meses(linha_meses):
    """
    Mapeia cada índice de coluna ao seu mês correspondente.
    Normaliza textos verticais.
    """
    meses_colunas = {}
    mes_atual = None
    for i, celula in enumerate(linha_meses):
        valor = str(celula).strip() if celula else ""
        if valor:
            mes_normalizado = normalizar_texto_mes(valor)
            if mes_normalizado:
                mes_atual = mes_normalizado
        if mes_atual:
            if mes_atual not in meses_colunas:
                meses_colunas[mes_atual] = []
            meses_colunas[mes_atual].append(i)
    return meses_colunas


# =========================================================
# ANÁLISE DE FREQUÊNCIA POR MÊS (frequência da turma)
# =========================================================

def analisar_frequencia_por_mes(caminho_pdf, peso_disciplina=None):
    """
    Analisa a frequência de todos os alunos por mês.
    - Conta o valor real de faltas (2 ou 4 períodos).
    - Trata 'J' como presença (não conta como falta).
    - Normaliza meses escritos verticalmente.
    """
    metadados_pdf = extrair_metadados_pdf(caminho_pdf)

    if peso_disciplina is None:
        peso_disciplina = inferir_peso_disciplina(metadados_pdf.get("Carga Horária", ""))

    with pdfplumber.open(caminho_pdf) as pdf:
        tabela = encontrar_tabela_frequencia(pdf)
        if not tabela or len(tabela) < 3:
            return None

        linha_meses = tabela[0]
        linha_dias = tabela[1]
        meses_colunas = mapear_colunas_meses(linha_meses)
        if not meses_colunas:
            return None

        resultados = []
        for linha in tabela[2:]:
            if not linha or len(linha) < 2:
                continue
            matricula = str(linha[0]).strip()
            if not matricula.isdigit() or len(matricula) < 5:
                continue

            nome = str(linha[1]).strip().replace("\n", " ")
            registro = metadados_pdf.copy()
            registro.update({"Matrícula": matricula, "Nome": nome})

            total_aulas_geral = 0
            total_faltas_geral = 0

            for mes, colunas in meses_colunas.items():
                aulas_mes = 0
                faltas_mes = 0
                # Datas das faltas e sinal de possível evasão: mesma leitura
                # da antiga "Busca por mês" (marcação literal, todas as células
                # do mês — inclusive as sem número de dia no cabeçalho).
                datas_faltas = []
                marcacoes = []

                for idx in colunas:
                    if idx >= len(linha):
                        continue
                    dia = str(linha_dias[idx]).strip() if idx < len(linha_dias) else ""
                    marcador = str(linha[idx]).strip() if linha[idx] is not None else ""
                    marcacoes.append(marcador)
                    if marcador.isdigit() and int(marcador) > 0:
                        datas_faltas.append(f"{dia} ({marcador}f)")
                    if not dia or not dia.isdigit():
                        continue

                    valor = str(linha[idx]).strip().upper() if linha[idx] else ""
                    if valor == "":
                        continue

                    if valor == "*":
                        aulas_mes += peso_disciplina        # presença: conta os períodos do dia
                    elif valor.isdigit() and int(valor) > 0:
                        aulas_mes += peso_disciplina        # falta: conta os períodos do dia como aula
                        faltas_mes += peso_disciplina       # falta: registra os períodos do dia, não o valor literal
                    elif valor == "J":
                        aulas_mes += peso_disciplina        # justificado: conta como presença

                registro[f"{mes.capitalize()}_Total_Aulas"] = aulas_mes
                registro[f"{mes.capitalize()}_Dias_Faltados"] = faltas_mes
                registro[f"{mes.capitalize()}_%_Presença"] = (
                    round(((aulas_mes - faltas_mes) / aulas_mes) * 100, 2)
                    if aulas_mes > 0 else 0.0
                )
                registro[f"{mes.capitalize()}_Datas_Faltas"] = ", ".join(datas_faltas)
                # Possível evasão no mês: as duas últimas marcações preenchidas
                # são faltas (números) — o aluno parou de vir no fim do mês.
                preenchidas = [m for m in marcacoes if m != ""]
                registro[f"{mes.capitalize()}_Evasao"] = (
                    len(preenchidas) >= 2 and all(m.isdigit() for m in preenchidas[-2:])
                )
                total_aulas_geral += aulas_mes
                total_faltas_geral += faltas_mes

            registro["Total_Aulas_Geral"] = total_aulas_geral
            registro["Total_Dias_Faltados_Geral"] = total_faltas_geral
            registro["%_Presença_Geral"] = (
                round(((total_aulas_geral - total_faltas_geral) / total_aulas_geral) * 100, 2)
                if total_aulas_geral > 0 else 0.0
            )
            resultados.append(registro)

    return pd.DataFrame(resultados) if resultados else None


def organizar_colunas_frequencia(df_final):
    colunas_fixas = [
        "Disciplina", "Código", "Ano/Semestre", "Curso", "Carga Horária",
        "Coordenador do Curso", "Docente", "Matrícula Docente", "Matrícula", "Nome"
    ]
    ordem_meses = [
        "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
        "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro", "Janeiro"
    ]

    colunas_ordenadas_meses = []
    for mes in ordem_meses:
        for suf in ["_Total_Aulas", "_Dias_Faltados", "_%_Presença", "_Datas_Faltas", "_Evasao"]:
            col = f"{mes}{suf}"
            if col in df_final.columns:
                colunas_ordenadas_meses.append(col)

    colunas_finais = colunas_fixas + colunas_ordenadas_meses + [
        "Total_Aulas_Geral", "Total_Dias_Faltados_Geral", "%_Presença_Geral"
    ]
    return df_final[[c for c in colunas_finais if c in df_final.columns]]


# =========================================================
# PARSER DE HISTÓRICO ESCOLAR (SIGAA / IFFar) — status individual do aluno
#
# Portado de teste_parser/parser.py (ver teste_parser/DOCUMENTACAO.md).
# Lê o PDF de "Histórico Escolar" e devolve o status atualizado de UM aluno:
# disciplinas aprovadas, a cursar, reprovações por falta/média, índices, etc.
#
# Decisões de projeto importantes:
#  - O nome do aluno na página 1 vem com cada caractere duplicado (artefato de
#    renderização do campo em negrito). `_dedupe_bold_artifact` só corrige quando
#    o padrão bate em TODOS os tokens, para não corromper letras dobradas legítimas.
#  - A tabela de componentes tem duas colunas de nota ("Nota Mín" e "Média") que
#    divergem; a semântica de "Nota Mín" é incerta. A classificação de
#    aprovação/reprovação usa EXCLUSIVAMENTE a coluna "Situação".
#  - Percentuais de reprovação usam como base só componentes avaliados nesta
#    oferta (APR+REP+REPF+REPMF). REPMF conta nos dois percentuais ao mesmo tempo.
# =========================================================

SITUACAO_LABELS = {
    "APR": "Aprovado",
    "REP": "Reprovado por média",
    "REPF": "Reprovado por falta",
    "REPMF": "Reprovado por média e falta",
    "MATR": "Matriculado (em curso)",
    "CANC": "Cancelado",
    "DISP": "Dispensado",
    "CUMP": "Cumpriu (componente equivalente)",
    "TRANC": "Trancado",
}

HIST_APROVEITADAS = {"APR", "DISP", "CUMP"}
HIST_REPROVACOES = {"REP", "REPF", "REPMF"}
HIST_EM_CURSO = {"MATR"}
HIST_AVALIADOS = {"APR", "REP", "REPF", "REPMF"}


def _dedupe_bold_artifact(s):
    """Remove duplicação de caracteres da Pág.1 (bug de negrito), só quando o
    padrão se confirma em TODOS os tokens (separados por espaço)."""
    tokens = s.split(" ")
    for t in tokens:
        if len(t) == 0:
            continue
        if len(t) % 2 != 0:
            return s
        if any(t[i] != t[i + 1] for i in range(0, len(t), 2)):
            return s
    return " ".join(t[0::2] for t in tokens)


@dataclass
class Componente:
    periodo: str
    codigo: str
    nome: str
    docentes: str
    carga_horaria: Optional[int]
    freq_pct: Optional[float]
    nota_bruta: Optional[float]   # coluna "Nota Mín" no PDF — semântica incerta
    media: Optional[float]        # coluna "Média"
    situacao: str


@dataclass
class Pendente:
    codigo: str
    nome: str
    matriculado_atualmente: bool
    carga_horaria: Optional[int]


@dataclass
class HistoricoAluno:
    nome: str
    matricula: str
    curso: str
    status_matricula: str
    periodo_ingresso: str
    forma_ingresso: str
    periodo_atual: Optional[int]
    prazo_padrao: Optional[str]
    prazo_maximo: Optional[str]
    mc: Optional[float]
    ira: Optional[float]
    componentes: list = field(default_factory=list)
    pendentes: list = field(default_factory=list)


def _hist_to_float(s):
    if s is None:
        return None
    s = str(s).strip().replace(",", ".")
    if s in ("", "--", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _hist_to_int(s):
    if s is None:
        return None
    m = re.search(r"\d+", str(s).strip())
    return int(m.group()) if m else None


def _parse_historico_pagina1(texto):
    def find(pattern, default=None, flags=re.MULTILINE):
        m = re.search(pattern, texto, flags)
        return m.group(1).strip() if m else default

    nome_raw = find(r"Nome:\s*(.+?)\s*Matrícula:")
    nome = _dedupe_bold_artifact(nome_raw) if nome_raw else None

    prazo_padrao = prazo_maximo = None
    m = re.search(r"Máximo\)\s*:\s*([\d.]+)\s*/\s*([\d.]+)", texto)
    if m:
        prazo_padrao, prazo_maximo = m.group(1), m.group(2)

    mc = ira = None
    m = re.search(r"MC:\s*([\d.]+)\s*IRA:\s*([\d.]+)", texto)
    if m:
        mc, ira = _hist_to_float(m.group(1)), _hist_to_float(m.group(2))

    return dict(
        nome=nome,
        matricula=find(r"Matrícula:\s*(\d+)"),
        curso=find(r"^Curso:\s*(.+)$"),
        status_matricula=find(r"^Status:\s*(\S+)"),
        periodo_ingresso=find(r"Ano / Período Letivo Inicial:\s*([\d.]+)"),
        forma_ingresso=find(r"Forma de Ingresso:\s*(.+)$"),
        periodo_atual=_hist_to_int(find(r"Período Letivo Atual:\s*(\d+)")),
        prazo_padrao=prazo_padrao, prazo_maximo=prazo_maximo, mc=mc, ira=ira,
    )


def _parse_historico_componentes(rows):
    out = []
    for row in rows:
        if len(row) < 11:
            continue
        periodo, _marc, codigo, nome_docente, _hora, ch, _turma, freq, nota, media, situ = row[:11]
        if not codigo or not situ:
            continue
        # A célula "NOME DO COMPONENTE\nDOCENTE(S)" — 1ª linha é o nome,
        # o restante são os docentes (pode haver mais de um). Remove o sufixo
        # de carga horária que às vezes acompanha o nome do docente ("(72h)").
        partes = [p.strip() for p in (nome_docente or "").split("\n") if p.strip()]
        docentes = " / ".join(partes[1:])
        docentes = re.sub(r"\s*[-–]?\s*\(?\s*\d+\s*h\s*\)?", "", docentes)
        docentes = re.sub(r"\s{2,}", " ", docentes).strip(" /-–—")
        out.append(Componente(
            periodo=(periodo or "").strip(),
            codigo=codigo.strip(),
            nome=partes[0] if partes else "",
            docentes=docentes,
            carga_horaria=_hist_to_int(ch),
            freq_pct=_hist_to_float(freq),
            nota_bruta=_hist_to_float(nota),
            media=_hist_to_float(media),
            situacao=(situ or "").strip(),
        ))
    return out


def _parse_historico_pendentes(rows):
    out = []
    for row in rows[2:]:  # pula linha-título e cabeçalho
        if len(row) < 3 or not row[0]:
            continue
        codigo, nome_ch, ch = row[0], row[1] or "", row[2]
        out.append(Pendente(
            codigo=codigo.strip(),
            nome=nome_ch.replace("Matriculado", "").strip(),
            matriculado_atualmente="Matriculado" in nome_ch,
            carga_horaria=_hist_to_int(ch),
        ))
    return out


def parse_historico(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        dados = _parse_historico_pagina1(pdf.pages[0].extract_text() or "")

        componentes, pendentes = [], []
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table or not table[0]:
                    continue
                header0 = str(table[0][0] or "")
                if len(table[0]) == 11:
                    componentes += _parse_historico_componentes(table[1:])
                elif header0.startswith("Componentes Curriculares Obrigatórios Pendentes"):
                    pendentes += _parse_historico_pendentes(table)

    return HistoricoAluno(componentes=componentes, pendentes=pendentes, **dados)


def _ordenar_periodo(p):
    """Chave de ordenação para períodos "AAAA.S" (ex.: "2026.1")."""
    m = re.match(r"(\d{4})\.(\d)", str(p or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _desempenho_por_semestre(componentes):
    por = {}
    for c in componentes:
        p = (c.periodo or "").strip()
        if not p:
            continue
        d = por.setdefault(p, {
            "periodo": p, "componentes": 0, "aprovados": 0,
            "reprovados": 0, "em_curso": 0, "_medias": [],
        })
        d["componentes"] += 1
        if c.situacao == "APR":
            d["aprovados"] += 1
        elif c.situacao in HIST_REPROVACOES:
            d["reprovados"] += 1
        elif c.situacao == "MATR":
            d["em_curso"] += 1
        if c.media is not None and c.situacao in HIST_AVALIADOS:
            d["_medias"].append(c.media)

    saida = []
    for d in sorted(por.values(), key=lambda x: _ordenar_periodo(x["periodo"])):
        medias = d.pop("_medias")
        d["media_semestre"] = round(sum(medias) / len(medias), 2) if medias else None
        # taxas sobre os componentes avaliados no semestre (aprovados + reprovados)
        avaliados = d["aprovados"] + d["reprovados"]
        d["pct_aprovacao"] = round(d["aprovados"] / avaliados * 100, 1) if avaliados else None
        d["pct_reprovacao"] = round(d["reprovados"] / avaliados * 100, 1) if avaliados else None
        saida.append(d)
    return saida


def _disciplinas_a_cursar(h, docente_por_codigo):
    """Disciplinas que o aluno ainda precisa cursar / está cursando.

    Base: os "Componentes Curriculares Obrigatórios Pendentes" do PDF. Como
    essa tabela só lista o que está no PPC, disciplinas ELETIVAS em que o
    aluno está matriculado agora (situação MATR no histórico) não aparecem
    ali — então são acrescentadas a partir dos próprios componentes, desde
    que ainda não estejam na lista de pendentes.
    """
    saida = [
        {"codigo": p.codigo, "componente": p.nome,
         "carga_horaria": p.carga_horaria, "matriculado_atualmente": p.matriculado_atualmente,
         "docente": docente_por_codigo.get(p.codigo, "")}
        for p in h.pendentes
    ]
    ja_listados = {p.codigo for p in h.pendentes}
    for c in h.componentes:
        if c.situacao == "MATR" and c.codigo not in ja_listados:
            ja_listados.add(c.codigo)
            saida.append({
                "codigo": c.codigo, "componente": c.nome,
                "carga_horaria": c.carga_horaria, "matriculado_atualmente": True,
                "docente": docente_por_codigo.get(c.codigo, c.docentes or ""),
            })
    return saida


def resumo_status(h):
    contagem = {}
    for c in h.componentes:
        contagem[c.situacao] = contagem.get(c.situacao, 0) + 1

    aprovados = sum(v for k, v in contagem.items() if k in HIST_APROVEITADAS)
    reprovados_total = sum(v for k, v in contagem.items() if k in HIST_REPROVACOES)
    em_curso = sum(v for k, v in contagem.items() if k in HIST_EM_CURSO)

    base_avaliacao = sum(contagem.get(k, 0) for k in HIST_AVALIADOS)
    n_repf = contagem.get("REPF", 0)
    n_rep = contagem.get("REP", 0)
    n_repmf = contagem.get("REPMF", 0)

    def pct(numerador):
        return round(numerador / base_avaliacao * 100, 1) if base_avaliacao else None

    # Carga horária: concluída (APR/DISP/CUMP), em curso (MATR) e pendente
    # (obrigatórias que faltam). % de conclusão é estimado por CH sobre
    # (concluída + pendente obrigatória).
    ch_concluida = sum(c.carga_horaria or 0 for c in h.componentes if c.situacao in HIST_APROVEITADAS)
    ch_em_curso = sum(c.carga_horaria or 0 for c in h.componentes if c.situacao == "MATR")
    ch_pendente = sum(p.carga_horaria or 0 for p in h.pendentes)
    base_ch = ch_concluida + ch_pendente
    pct_conclusao = round(ch_concluida / base_ch * 100, 1) if base_ch else None

    # Docente por código (do último componente cursado desse código que traga docente).
    docente_por_codigo = {}
    for c in h.componentes:
        if c.docentes:
            docente_por_codigo[c.codigo] = c.docentes

    return {
        "aluno": h.nome,
        "matricula": h.matricula,
        "curso": h.curso,
        "status_matricula": h.status_matricula,
        "periodo_ingresso": h.periodo_ingresso,
        "forma_ingresso": h.forma_ingresso,
        "periodo_atual": h.periodo_atual,
        "prazo_padrao": h.prazo_padrao,
        "prazo_maximo": h.prazo_maximo,
        "indices": {"MC": h.mc, "IRA": h.ira},
        "componentes_por_situacao": {
            SITUACAO_LABELS.get(k, k): v for k, v in sorted(contagem.items())
        },
        "resumo": {
            "total_componentes_no_historico": len(h.componentes),
            "aprovados_ou_equivalente": aprovados,
            "reprovados_total": reprovados_total,
            "reprovados_por_falta": n_repf,
            "reprovados_por_media": n_rep,
            "reprovados_por_media_e_falta": n_repmf,
            "em_curso_atualmente": em_curso,
            "pendentes_curriculo": len(h.pendentes),
        },
        "carga_horaria": {
            "concluida": ch_concluida,
            "em_curso": ch_em_curso,
            "pendente": ch_pendente,
            "pct_conclusao_estimado": pct_conclusao,
            "obs": (
                "estimativa por CH: concluída ÷ (concluída + pendente obrigatória); "
                "não usa a CH total do currículo (não extraída do PDF)"
            ),
        },
        "percentuais": {
            "base_calculo": base_avaliacao,
            "criterio_base": (
                "componentes com resultado avaliado nesta oferta (APR+REP+REPF+REPMF); "
                "exclui dispensas/equivalências (DISP/CUMP), em curso (MATR) e "
                "cancelamentos/trancamentos (CANC/TRANC)"
            ),
            "reprovacao_por_falta_pct": pct(n_repf + n_repmf),
            "reprovacao_por_media_pct": pct(n_rep + n_repmf),
            "obs_repmf": (
                "REPMF (reprovado por média e por falta) é contado nos dois percentuais "
                "acima ao mesmo tempo, por isso a soma pode ultrapassar o total de reprovações."
            ),
        },
        "disciplinas_aprovadas": [
            {"codigo": c.codigo, "componente": c.nome, "periodo": c.periodo,
             "carga_horaria": c.carga_horaria, "media": c.media, "docente": c.docentes}
            for c in h.componentes if c.situacao == "APR"
        ],
        "disciplinas_a_cursar": _disciplinas_a_cursar(h, docente_por_codigo),
        "reprovacoes_detalhe": [
            {"codigo": c.codigo, "componente": c.nome, "periodo": c.periodo,
             "carga_horaria": c.carga_horaria, "media": c.media,
             "freq_pct": c.freq_pct, "situacao": c.situacao, "docente": c.docentes}
            for c in h.componentes if c.situacao in HIST_REPROVACOES
        ],
        "desempenho_por_semestre": _desempenho_por_semestre(h.componentes),
    }


# =========================================================
# NÍVEIS DE ENSINO — operações separadas por nível
# =========================================================
#
# O nível de quem usa vem da modalidade aprovada do coordenador (fixo) ou,
# para o admin, do header X-Nivel (seletor no topo da tela) — ver
# _nivel_da_requisicao. Cada nível declara as próprias operações:
#   - periodos_por_ch / periodos_padrao: sugestão de períodos por dia de aula
#     a partir da CH semestral da disciplina. A LEITURA dos diários (frequência
#     geral e busca por mês) é a mesma para todos os níveis;
#   - historico: função que lê o PDF de histórico escolar e devolve o resumo
#     do aluno, ou None quando a análise individual ainda não existe para o
#     nível (hoje só o Superior tem leitor de histórico).
# Pós-graduação usa a tabela do superior e EJA a do técnico até termos
# diários reais desses níveis para conferir.

PERIODOS_SUPERIOR = {36: 2, 72: 4}
PERIODOS_TECNICO = {40: 1, 80: 2, 120: 3}


def analisar_historico_superior(caminho_pdf):
    """Histórico escolar do SIGAA de graduação (componentes, pendências,
    índices MC/IRA) → resumo do aluno."""
    return resumo_status(parse_historico(caminho_pdf))


NIVEIS = {
    "tecnico_integrado": {"periodos_por_ch": PERIODOS_TECNICO, "periodos_padrao": 1, "historico": None},
    "tecnico_subsequente": {"periodos_por_ch": PERIODOS_TECNICO, "periodos_padrao": 1, "historico": None},
    "superior": {"periodos_por_ch": PERIODOS_SUPERIOR, "periodos_padrao": 2, "historico": analisar_historico_superior},
    "eja": {"periodos_por_ch": PERIODOS_TECNICO, "periodos_padrao": 1, "historico": None},
    "pos_graduacao": {"periodos_por_ch": PERIODOS_SUPERIOR, "periodos_padrao": 2, "historico": None},
}
assert set(NIVEIS) == set(MODALIDADES), "NIVEIS e MODALIDADES devem ter as mesmas chaves"


def config_nivel(nivel):
    return NIVEIS.get(nivel) or NIVEIS[NIVEL_PADRAO]


def rotulo_nivel(nivel):
    return MODALIDADES.get(nivel, MODALIDADES[NIVEL_PADRAO])


def periodos_sugeridos(nivel, carga_horaria_str):
    """Períodos/dia sugeridos pela CH no nível, ou None se a CH não é reconhecida."""
    return config_nivel(nivel)["periodos_por_ch"].get(_ch_int(carga_horaria_str))


def recursos_do_nivel(nivel):
    return {"analise_individual": config_nivel(nivel)["historico"] is not None}


# =========================================================
# ROTAS
# =========================================================

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok",
        "message": "API v5.1 - Nível e períodos por dia configuráveis por disciplina."
    })


@app.route("/check-disciplines", methods=["POST", "OPTIONS"])
@exige_aprovado
def check_disciplines():
    """
    ETAPA 1 — Pré-análise dos PDFs enviados.

    Retorna metadados de cada arquivo:
      - disciplina, código, carga_horaria, docente, ano_semestre
      - requer_confirmacao: True se CH == 72 (ambíguo: 2 ou 4 períodos/noite)
      - peso_sugerido: sugestão pela CH, conforme o nível (ver NIVEIS)

    O frontend usa essa resposta para exibir a tela de confirmação
    antes de chamar /analyze ou /analyze-frequency.

    Disciplinas com mesmo código são deduplicadas na resposta.
    """
    if request.method == "OPTIONS":
        return "", 200

    arquivos = request.files.getlist("arquivos")
    if not arquivos:
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    resultado = []
    vistos = set()

    with tempfile.TemporaryDirectory() as tmp:
        for f in arquivos:
            if not f or f.filename == "":
                continue
            path = os.path.join(tmp, secure_filename(f.filename))
            f.save(path)

            try:
                meta = extrair_metadados_pdf(path)
                codigo = meta.get("Código", "")

                # Deduplicação por código de disciplina
                if codigo in vistos:
                    continue
                vistos.add(codigo)

                ch_str = meta.get("Carga Horária", "0")
                ch = _ch_int(ch_str)

                resultado.append({
                    "arquivo": f.filename,
                    "disciplina": meta["Disciplina"],
                    "codigo": codigo,
                    "carga_horaria": ch_str,
                    "docente": meta["Docente"],
                    "ano_semestre": meta["Ano/Semestre"],
                    # O frontend sempre exibe a tela de configuração; este campo
                    # apenas sinaliza CH ambígua (pode ser distribuída em >1 dia).
                    "requer_confirmacao": ch in (72, 80, 120),
                    # Sugestão inicial de períodos/dia (None se CH desconhecida).
                    "peso_sugerido": periodos_sugeridos(request.nivel, ch_str),
                    # nível de quem está usando (fixo por coordenador)
                    "nivel_sugerido": request.nivel,
                })

            except Exception as e:
                print(f"Erro ao ler metadados de {f.filename}: {e}")
                resultado.append({
                    "arquivo": f.filename,
                    "disciplina": "Erro ao ler",
                    "codigo": "",
                    "carga_horaria": "",
                    "docente": "",
                    "ano_semestre": "",
                    "requer_confirmacao": False,
                    "peso_sugerido": config_nivel(request.nivel)["periodos_padrao"],
                    "nivel_sugerido": request.nivel,
                    "erro": str(e)
                })

    return jsonify(resultado)


@app.route("/analyze-frequency", methods=["POST", "OPTIONS"])
@exige_aprovado
def analyze_frequency():
    """
    ETAPA 2B — Análise completa de frequência por mês.

    Parâmetros form-data:
      - arquivos : lista de PDFs
      - pesos    : JSON com mapa código → períodos
                   ex: '{"08023217": 4, "08023100": 2}'
                   Se não informado, infere automaticamente pela CH.
    """
    if request.method == "OPTIONS":
        return "", 200

    pesos_raw = request.form.get("pesos", "{}")
    arquivos = request.files.getlist("arquivos")

    if not arquivos:
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    try:
        pesos_por_codigo = json.loads(pesos_raw)
    except (json.JSONDecodeError, TypeError):
        pesos_por_codigo = {}

    lista_dfs = []
    with tempfile.TemporaryDirectory() as tmp:
        for f in arquivos:
            if not f or f.filename == "":
                continue
            path = os.path.join(tmp, secure_filename(f.filename))
            f.save(path)
            try:
                meta = extrair_metadados_pdf(path)
                codigo = meta.get("Código", "")
                peso = inferir_peso_disciplina(
                    meta.get("Carga Horária", ""),
                    pesos_por_codigo=pesos_por_codigo,
                    codigo=codigo,
                    nivel=request.nivel,
                )
                df = analisar_frequencia_por_mes(path, peso_disciplina=peso)
                if df is not None:
                    lista_dfs.append(df)
            except Exception as e:
                print(f"Erro ao processar {f.filename}: {e}")

    if lista_dfs:
        df_final = pd.concat(lista_dfs, ignore_index=True)
        df_final = organizar_colunas_frequencia(df_final)
        return jsonify(df_final.fillna("").to_dict(orient="records"))

    return jsonify([])


@app.route("/analyze-historico", methods=["POST", "OPTIONS"])
@exige_aprovado
def analyze_historico():
    """
    Avaliação individual do aluno a partir do PDF de Histórico Escolar (SIGAA).
    O leitor depende do nível (NIVEIS[nivel]["historico"]); níveis sem leitor
    respondem 501.

    Parâmetros form-data:
      - arquivo : um único PDF de "Histórico Escolar"

    Retorna o dicionário de `resumo_status` (status atualizado do aluno).
    """
    if request.method == "OPTIONS":
        return "", 200

    f = request.files.get("arquivo") or (request.files.getlist("arquivos") or [None])[0]
    if not f or f.filename == "":
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    analisar = config_nivel(request.nivel)["historico"]
    if analisar is None:
        return jsonify({"erro": "A análise individual do aluno ainda não está disponível para o nível "
                                f"{rotulo_nivel(request.nivel)}."}), 501

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, secure_filename(f.filename))
        f.save(path)
        try:
            return jsonify(analisar(path))
        except Exception as e:
            print(f"Erro ao processar histórico {f.filename}: {e}")
            return jsonify({"erro": f"Não foi possível ler o histórico: {e}"}), 422


if __name__ == "__main__":
    # host 0.0.0.0: sem isso o Flask só escuta em 127.0.0.1 e fica
    # inacessível de fora do container (Docker) mesmo com a porta mapeada.
    app.run(debug=True, host="0.0.0.0", port=5001)