import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})


# =========================================================
# FUNÇÕES AUXILIARES DE METADADOS
# =========================================================

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


def extrair_metadados_pdf(caminho_pdf):
    metadados = {
        "Centro": "", "Curso": "", "Coordenador do Curso": "",
        "Código": "", "Disciplina": "", "Carga Horária": "",
        "Ano/Semestre": "", "Docente": "", "Matrícula Docente": ""
    }

    with pdfplumber.open(caminho_pdf) as pdf:
        # --- BUSCA DE DADOS GERAIS (Sempre na Página 1) ---
        if len(pdf.pages) >= 1:
            texto_p1 = pdf.pages[0].extract_text() or ""
            linhas_p1 = [l.strip() for l in texto_p1.split("\n") if l.strip()]
            
            rotulos_p1 = [
                "Centro:", "Curso:", "Coordenador de Curso:", 
                "Coordenador do Curso:", "Código:", "Disciplina:", 
                "Créditos:", "Carga Horária:", "Turma:", "Ano/Semestre:"
            ]
            
            metadados["Centro"] = extrair_valor_rotulo_multilinha(linhas_p1, "Centro:", rotulos_p1)
            metadados["Curso"] = extrair_valor_rotulo_multilinha(linhas_p1, "Curso:", ["Coordenador de Curso:", "Coordenador do Curso:", "Código:"])
            metadados["Coordenador do Curso"] = extrair_valor_rotulo_multilinha(linhas_p1, "Coordenador de Curso:", rotulos_p1)
            metadados["Código"] = extrair_valor_rotulo_multilinha(linhas_p1, "Código:", rotulos_p1)
            
            disciplina_bruta = extrair_valor_rotulo_multilinha(linhas_p1, "Disciplina:", ["Créditos:", "Carga Horária:", "Código:"])
            metadados["Disciplina"] = disciplina_bruta.split("Créditos:")[0].strip()
            metadados["Carga Horária"] = extrair_valor_rotulo_multilinha(linhas_p1, "Carga Horária:", rotulos_p1)

            # Captura Ano/Semestre (Ex: 2026.1)
            for linha in linhas_p1:
                m = re.search(r"(\d{4}\.\d)", linha)
                if m:
                    metadados["Ano/Semestre"] = m.group(1)
                    break

        # --- BUSCA DE DOCENTE (Tenta na Página 1, se não achar, tenta na Página 2) ---
        # Analisamos as duas primeiras páginas para o docente
        paginas_para_busca = pdf.pages[:2]
        
        for num_pag, pagina in enumerate(paginas_para_busca):
            # Se já encontramos o docente na página anterior, não precisa buscar na próxima
            if metadados["Docente"] and metadados["Matrícula Docente"]:
                break
                
            texto = pagina.extract_text() or ""
            linhas = [l.strip() for l in texto.split("\n") if l.strip()]

            for i, linha in enumerate(linhas):
                # Busca por Docente (Captura nome e limpa carga horária)
                if "Docente" in linha:
                    valor = re.split(r"Docente\(s\)|Docente:", linha, flags=re.IGNORECASE)[-1].strip()
                    # Se o nome estiver na linha de baixo
                    if not valor and (i + 1) < len(linhas):
                        valor = linhas[i+1].strip()
                    
                    if valor:
                        metadados["Docente"] = re.split(r"\s-\s\d+h", valor)[0].strip()

                # Busca por Matrícula
                if "Matrícula" in linha and not metadados["Matrícula Docente"]:
                    valor_m = linha.replace("Matrícula", "").strip()
                    # Se a matrícula estiver na linha de baixo
                    if not valor_m and (i + 1) < len(linhas):
                        valor_m = linhas[i+1].strip()
                    
                    m = re.search(r"(\d{5,})", valor_m)
                    if m:
                        metadados["Matrícula Docente"] = m.group(1)

    return metadados


def extrair_nome_disciplina(pdf):
    primeira_pagina = pdf.pages[0].extract_text() or ""
    linhas = [linha.strip() for linha in primeira_pagina.split("\n") if linha.strip()]
    rotulos_p1 = ["Centro:", "Curso:", "Coordenador de Curso:", "Código:", "Disciplina:", "Créditos:", "Carga Horária:", "Turma:", "Ano/Semestre:", "Horário:"]
    nome_disciplina = extrair_valor_rotulo_multilinha(linhas, "Disciplina:", rotulos_p1)
    return nome_disciplina or "Não identificada"


# =========================================================
# BLOCO 1 - ANÁLISE DE EVASÃO
# =========================================================

def analisar_faltas_detalhado(caminho_pdf, mes_alvo):
    mes_alvo = mes_alvo.lower()
    alunos_criticos = []
    metadados_pdf = extrair_metadados_pdf(caminho_pdf)

    with pdfplumber.open(caminho_pdf) as pdf:
        tabela = None
        for pagina in pdf.pages:
            texto_pagina = pagina.extract_text() or ""
            if "Lista de Freq" in texto_pagina or "Lista de Frequ" in texto_pagina:
                tabela = pagina.extract_table()
                if tabela: break

        if not tabela or len(tabela) < 2: return None

        linha_meses = tabela[0]
        linha_dias = tabela[1]
        indices_mes = []
        mes_atual = ""

        for i, celula in enumerate(linha_meses):
            if celula and str(celula).strip():
                mes_atual = str(celula).strip().lower()
            if mes_atual and mes_alvo in mes_atual:
                indices_mes.append(i)

        if not indices_mes: return None

        for linha in tabela[2:]:
            if not linha or len(linha) < 2 or not linha[0]: continue
            matricula_bruta = str(linha[0]).strip()
            if not matricula_bruta.isdigit() or len(matricula_bruta) < 5: continue

            nome = str(linha[1]).strip().replace("\n", " ")
            faltas_no_mes_contagem = 0
            datas_faltas = []
            sequencia_bruta = []

            for idx in indices_mes:
                if idx >= len(linha): continue
                marcador = str(linha[idx]).strip() if linha[idx] is not None else ""
                dia = str(linha_dias[idx]).strip() if idx < len(linha_dias) else ""
                sequencia_bruta.append(marcador)
                if marcador.isdigit() and int(marcador) > 0:
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

def encontrar_tabela_frequencia(pdf):
    for pagina in pdf.pages:
        texto = pagina.extract_text() or ""
        if "Lista de Freq" in texto or "Lista de Frequ" in texto:
            tabela = pagina.extract_table()
            if tabela and len(tabela) > 2: return tabela
    return None


def mapear_colunas_meses(linha_meses):
    meses_colunas = {}
    mes_atual = None
    for i, celula in enumerate(linha_meses):
        valor = str(celula).strip() if celula else ""
        if valor: mes_atual = valor.lower()
        if mes_atual:
            if mes_atual not in meses_colunas: meses_colunas[mes_atual] = []
            meses_colunas[mes_atual].append(i)
    return meses_colunas


def analisar_frequencia_por_mes(caminho_pdf):
    metadados_pdf = extrair_metadados_pdf(caminho_pdf)
    with pdfplumber.open(caminho_pdf) as pdf:
        tabela = encontrar_tabela_frequencia(pdf)
        if not tabela or len(tabela) < 3: return None

        linha_meses = tabela[0]
        linha_dias = tabela[1]
        meses_colunas = mapear_colunas_meses(linha_meses)
        if not meses_colunas: return None

        resultados = []
        for linha in tabela[2:]:
            if not linha or len(linha) < 2: continue
            matricula = str(linha[0]).strip()
            if not matricula.isdigit() or len(matricula) < 5: continue
            
            nome = str(linha[1]).strip().replace("\n", " ")
            registro = metadados_pdf.copy()
            registro.update({"Matrícula": matricula, "Nome": nome})

            total_aulas_geral = 0
            total_dias_faltados_geral = 0

            for mes, colunas in meses_colunas.items():
                aulas_mes = 0
                faltas_mes = 0
                for idx in colunas:
                    if idx >= len(linha): continue
                    dia = str(linha_dias[idx]).strip() if idx < len(linha_dias) else ""
                    if not dia or not dia.isdigit(): continue
                    
                    valor = str(linha[idx]).strip().upper() if linha[idx] else ""
                    if valor == "": continue
                    
                    aulas_mes += 1
                    if valor.isdigit() and int(valor) > 0: faltas_mes += 1

                registro[f"{mes.capitalize()}_Total_Aulas"] = aulas_mes
                registro[f"{mes.capitalize()}_Dias_Faltados"] = faltas_mes
                registro[f"{mes.capitalize()}_%_Presença"] = round(((aulas_mes-faltas_mes)/aulas_mes)*100, 2) if aulas_mes > 0 else 0.0
                total_aulas_geral += aulas_mes
                total_dias_faltados_geral += faltas_mes

            registro["Total_Aulas_Geral"] = total_aulas_geral
            registro["Total_Dias_Faltados_Geral"] = total_dias_faltados_geral
            registro["%_Presença_Geral"] = round(((total_aulas_geral-total_dias_faltados_geral)/total_aulas_geral)*100, 2) if total_aulas_geral > 0 else 0.0
            resultados.append(registro)

    return pd.DataFrame(resultados) if resultados else None


def organizar_colunas_frequencia(df_final):
    colunas_fixas = ["Disciplina", "Código", "Ano/Semestre", "Curso", "Carga Horária", "Coordenador do Curso", "Docente", "Matrícula Docente", "Matrícula", "Nome"]
    ordem_meses = ["Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro", "Janeiro"]
    
    colunas_ordenadas_meses = []
    for mes in ordem_meses:
        for suf in ["_Total_Aulas", "_Dias_Faltados", "_%_Presença"]:
            if f"{mes}{suf}" in df_final.columns: colunas_ordenadas_meses.append(f"{mes}{suf}")

    colunas_finais = colunas_fixas + colunas_ordenadas_meses + ["Total_Aulas_Geral", "Total_Dias_Faltados_Geral", "%_Presença_Geral"]
    return df_final[[c for c in colunas_finais if c in df_final.columns]]


# =========================================================
# ROTAS
# =========================================================

@app.route("/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS": return "", 200
    mes_analise = request.form.get("mes", "Março")
    arquivos = request.files.getlist("arquivos")
    if not arquivos: return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    lista_dfs = []
    with tempfile.TemporaryDirectory() as tmp:
        for f in arquivos:
            if not f or f.filename == "": continue
            path = os.path.join(tmp, secure_filename(f.filename))
            f.save(path)
            try:
                df = analisar_faltas_detalhado(path, mes_analise)
                if df is not None: lista_dfs.append(df)
            except Exception as e: print(f"Erro: {e}")

    if lista_dfs:
        df_final = pd.concat(lista_dfs, ignore_index=True)
        # Agrupamento dinâmico baseado em todas as colunas de metadados
        metadados_cols = ["Disciplina", "Código", "Ano/Semestre", "Curso", "Carga Horária", "Coordenador do Curso", "Docente", "Matrícula Docente", "Matrícula", "Nome"]
        df_final = df_final.groupby(metadados_cols, as_index=False).agg({
            "Total Faltas (Mês)": "sum",
            "Datas das Faltas": lambda x: " // ".join([str(v) for v in x if str(v).strip()])
        })
        return jsonify(df_final.sort_values(by=["Nome", "Disciplina"]).to_dict(orient="records"))
    return jsonify([])


@app.route("/analyze-frequency", methods=["POST", "OPTIONS"])
def analyze_frequency():
    if request.method == "OPTIONS": return "", 200
    arquivos = request.files.getlist("arquivos")
    if not arquivos: return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    lista_dfs = []
    with tempfile.TemporaryDirectory() as tmp:
        for f in arquivos:
            path = os.path.join(tmp, secure_filename(f.filename))
            f.save(path)
            try:
                df = analisar_frequencia_por_mes(path)
                if df is not None: lista_dfs.append(df)
            except Exception as e: print(f"Erro: {e}")

    if lista_dfs:
        df_final = pd.concat(lista_dfs, ignore_index=True)
        df_final = organizar_colunas_frequencia(df_final)
        return jsonify(df_final.fillna("").to_dict(orient="records"))
    return jsonify([])

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "message": "API v3.5 - Ano/Semestre e Docente corrigidos."})

if __name__ == "__main__":
    app.run(debug=True)