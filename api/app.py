import os
import re
import json
import unicodedata
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})


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
            print(f"{coordenador}--------------")
            metadados["Código"] = extrair_valor_rotulo_multilinha(linhas_p1, "Código:", rotulos_p1)

            disciplina_bruta = extrair_valor_rotulo_multilinha(
                linhas_p1, "Disciplina:", ["Créditos:", "Carga Horária:", "Código:"]
            )
            metadados["Disciplina"] = disciplina_bruta.split("Créditos:")[0].strip()
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


def inferir_peso_disciplina(carga_horaria_str, pesos_por_codigo=None, codigo=None):
    """
    Determina o número de períodos por aula da disciplina.

    Regras:
      - Se o frontend enviou um peso explícito para este código → usa ele
      - CH 36h → sempre 2 períodos (automático, sem confirmação)
      - CH 72h → padrão 4, mas deve ser confirmado pelo frontend
      - Outros  → fallback 2
    """
    try:
        ch = int(str(carga_horaria_str).strip())
    except (ValueError, TypeError):
        ch = 0

    if pesos_por_codigo and codigo and codigo in pesos_por_codigo:
        return int(pesos_por_codigo[codigo])

    if ch == 36:
        return 2

    if ch == 72:
        return 4

    return 2


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
# BLOCO 1 - ANÁLISE DE EVASÃO (por mês)
# =========================================================

def analisar_faltas_detalhado(caminho_pdf, mes_alvo, peso_disciplina=None):
    """
    Analisa faltas de um mês específico, identificando alunos críticos.
    Conta o valor real da falta (2 ou 4 períodos).
    """
    mes_alvo_norm = sem_acento(mes_alvo.lower().strip())
    alunos_criticos = []
    metadados_pdf = extrair_metadados_pdf(caminho_pdf)

    if peso_disciplina is None:
        peso_disciplina = inferir_peso_disciplina(metadados_pdf.get("Carga Horária", ""))

    with pdfplumber.open(caminho_pdf) as pdf:
        tabela = None
        for pagina in pdf.pages:
            texto_pagina = pagina.extract_text() or ""
            if "Lista de Freq" in texto_pagina or "Lista de Frequ" in texto_pagina:
                tabela = pagina.extract_table()
                if tabela:
                    break

        if not tabela or len(tabela) < 2:
            return None

        linha_meses = tabela[0]
        linha_dias = tabela[1]
        indices_mes = []
        mes_atual = ""

        for i, celula in enumerate(linha_meses):
            if celula and str(celula).strip():
                mes_atual = normalizar_texto_mes(str(celula).strip())
            if mes_atual and mes_alvo_norm in sem_acento(mes_atual):
                indices_mes.append(i)

        if not indices_mes:
            return None

        for linha in tabela[2:]:
            if not linha or len(linha) < 2 or not linha[0]:
                continue
            matricula_bruta = str(linha[0]).strip()
            if not matricula_bruta.isdigit() or len(matricula_bruta) < 5:
                continue

            nome = str(linha[1]).strip().replace("\n", " ")
            faltas_no_mes_contagem = 0
            datas_faltas = []
            sequencia_bruta = []

            for idx in indices_mes:
                if idx >= len(linha):
                    continue
                marcador = str(linha[idx]).strip() if linha[idx] is not None else ""
                dia = str(linha_dias[idx]).strip() if idx < len(linha_dias) else ""
                sequencia_bruta.append(marcador)

                if marcador.upper() == "J":
                    pass  # justificado: não conta como falta
                elif marcador.isdigit() and int(marcador) > 0:
                    faltas_no_mes_contagem += int(marcador)
                    datas_faltas.append(f"{dia} ({marcador}f)")

            preenchidos = [m for m in sequencia_bruta if m != ""]
            if len(preenchidos) >= 2 and all(m.isdigit() for m in preenchidos[-2:]):
                registro = metadados_pdf.copy()
                registro.update({
                    "Matrícula": matricula_bruta,
                    "Nome": nome,
                    "Total Faltas (Mês)": faltas_no_mes_contagem,
                    "Datas das Faltas": ", ".join(datas_faltas)
                })
                alunos_criticos.append(registro)

    return pd.DataFrame(alunos_criticos) if alunos_criticos else None


# =========================================================
# BLOCO 2 - ANÁLISE DE FREQUÊNCIA POR MÊS
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

                for idx in colunas:
                    if idx >= len(linha):
                        continue
                    dia = str(linha_dias[idx]).strip() if idx < len(linha_dias) else ""
                    if not dia or not dia.isdigit():
                        continue

                    valor = str(linha[idx]).strip().upper() if linha[idx] else ""
                    if valor == "":
                        continue

                    if valor == "*":
                        aulas_mes += peso_disciplina        # presença: conta os 4 períodos
                    elif valor.isdigit() and int(valor) > 0:
                        aulas_mes += peso_disciplina        # ✅ falta: também conta os 4 períodos da noite
                        faltas_mes += peso_disciplina       # ✅ falta: registra os 4 períodos, não o valor literal
                    elif valor == "J":
                        aulas_mes += peso_disciplina        # justificado: conta como presença

                registro[f"{mes.capitalize()}_Total_Aulas"] = aulas_mes
                registro[f"{mes.capitalize()}_Dias_Faltados"] = faltas_mes
                registro[f"{mes.capitalize()}_%_Presença"] = (
                    round(((aulas_mes - faltas_mes) / aulas_mes) * 100, 2)
                    if aulas_mes > 0 else 0.0
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
        for suf in ["_Total_Aulas", "_Dias_Faltados", "_%_Presença"]:
            col = f"{mes}{suf}"
            if col in df_final.columns:
                colunas_ordenadas_meses.append(col)

    colunas_finais = colunas_fixas + colunas_ordenadas_meses + [
        "Total_Aulas_Geral", "Total_Dias_Faltados_Geral", "%_Presença_Geral"
    ]
    return df_final[[c for c in colunas_finais if c in df_final.columns]]


# =========================================================
# ROTAS
# =========================================================

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok",
        "message": "API v5.0 - Confirmação de períodos por disciplina."
    })


@app.route("/check-disciplines", methods=["POST", "OPTIONS"])
def check_disciplines():
    """
    ETAPA 1 — Pré-análise dos PDFs enviados.

    Retorna metadados de cada arquivo:
      - disciplina, código, carga_horaria, docente, ano_semestre
      - requer_confirmacao: True se CH == 72 (ambíguo: 2 ou 4 períodos/noite)
      - peso_sugerido: sugestão automática (36h → 2, 72h → 4)

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
                try:
                    ch = int(ch_str)
                except ValueError:
                    ch = 0

                resultado.append({
                    "arquivo": f.filename,
                    "disciplina": meta["Disciplina"],
                    "codigo": codigo,
                    "carga_horaria": ch_str,
                    "docente": meta["Docente"],
                    "ano_semestre": meta["Ano/Semestre"],
                    # True = frontend deve perguntar ao usuário
                    "requer_confirmacao": ch == 72,
                    # Sugestão padrão: 36h → 2 períodos, 72h → 4 períodos
                    "peso_sugerido": 2 if ch == 36 else 4
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
                    "peso_sugerido": 2,
                    "erro": str(e)
                })

    return jsonify(resultado)


@app.route("/analyze", methods=["POST", "OPTIONS"])
def analyze():
    """
    ETAPA 2A — Análise de evasão por mês.

    Parâmetros form-data:
      - arquivos : lista de PDFs
      - mes      : mês alvo (ex: "Março")
      - pesos    : JSON com mapa código → períodos
                   ex: '{"08023217": 4, "08023100": 2}'
                   Se não informado, infere automaticamente pela CH.
    """
    if request.method == "OPTIONS":
        return "", 200

    mes_analise = request.form.get("mes", "Março")
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
                    codigo=codigo
                )
                df = analisar_faltas_detalhado(path, mes_analise, peso_disciplina=peso)
                if df is not None:
                    lista_dfs.append(df)
            except Exception as e:
                print(f"Erro ao processar {f.filename}: {e}")

    if lista_dfs:
        df_final = pd.concat(lista_dfs, ignore_index=True)
        metadados_cols = [
            "Disciplina", "Código", "Ano/Semestre", "Curso", "Carga Horária",
            "Coordenador do Curso", "Docente", "Matrícula Docente", "Matrícula", "Nome"
        ]
        df_final = df_final.groupby(metadados_cols, as_index=False).agg({
            "Total Faltas (Mês)": "sum",
            "Datas das Faltas": lambda x: " // ".join([str(v) for v in x if str(v).strip()])
        })
        return jsonify(df_final.sort_values(by=["Nome", "Disciplina"]).to_dict(orient="records"))

    return jsonify([])


@app.route("/analyze-frequency", methods=["POST", "OPTIONS"])
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
                    codigo=codigo
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


if __name__ == "__main__":
    app.run(debug=True, port=5001)