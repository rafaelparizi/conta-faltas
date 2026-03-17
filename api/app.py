import os
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})


# =========================================================
# BLOCO 1 - ANÁLISE DE EVASÃO
# =========================================================

def analisar_faltas_detalhado(caminho_pdf, mes_alvo):
    mes_alvo = mes_alvo.lower()
    alunos_criticos = []

    with pdfplumber.open(caminho_pdf) as pdf:
        # 1. Extrai o nome da disciplina da primeira página
        primeira_pagina = pdf.pages[0].extract_text() or ""
        nome_disciplina = "Não identificada"
        linhas = [linha.strip() for linha in primeira_pagina.split("\n") if linha.strip()]

        for i, linha in enumerate(linhas):
            if "Disciplina:" in linha:
                parte_inicial = linha.split("Disciplina:", 1)[1].strip()
                partes = [parte_inicial] if parte_inicial else []
                j = i + 1

                while j < len(linhas):
                    proxima = linhas[j].strip()
                    if any(
                        proxima.startswith(k)
                        for k in ["Créditos:", "Carga Horária:", "Turma:", "Ano/Semestre:", "Horário:"]
                    ):
                        break
                    partes.append(proxima)
                    j += 1

                nome_disciplina = " ".join(partes).strip()
                break

        # 2. Procura automaticamente a página da Lista de Frequência
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
                mes_atual = str(celula).strip().lower()

            if mes_atual and mes_alvo in mes_atual:
                indices_mes.append(i)

        if not indices_mes:
            return None

        # 3. Processamento dos alunos
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
                data_dia = (
                    str(linha_dias[idx]).strip()
                    if idx < len(linha_dias) and linha_dias[idx] is not None
                    else ""
                )

                sequencia_bruta.append(marcador)

                if marcador.isdigit() and int(marcador) > 0:
                    faltas_no_mes_contagem += int(marcador)
                    datas_faltas.append(f"{data_dia} ({marcador}f)")

            # Regra de evasão: últimas 2 entradas preenchidas são faltas numéricas
            preenchidos = [m for m in sequencia_bruta if m != ""]

            evadindo = False
            if len(preenchidos) >= 2:
                ultimas_duas = preenchidos[-2:]
                if all(m.isdigit() for m in ultimas_duas):
                    evadindo = True

            if evadindo:
                alunos_criticos.append({
                    "Disciplina": nome_disciplina,
                    "Matrícula": matricula_bruta,
                    "Nome": nome,
                    "Total Faltas (Mês)": faltas_no_mes_contagem,
                    "Datas das Faltas": ", ".join(datas_faltas)
                })

    if alunos_criticos:
        return pd.DataFrame(alunos_criticos)

    return None


# =========================================================
# BLOCO 2 - ANÁLISE DE FREQUÊNCIA POR MÊS
# =========================================================

def extrair_nome_disciplina(pdf):
    primeira_pagina = pdf.pages[0].extract_text() or ""
    linhas = [linha.strip() for linha in primeira_pagina.split("\n") if linha.strip()]

    nome_disciplina = "Não identificada"

    for i, linha in enumerate(linhas):
        if "Disciplina:" in linha:
            parte_inicial = linha.split("Disciplina:", 1)[1].strip()
            partes = [parte_inicial] if parte_inicial else []

            j = i + 1
            while j < len(linhas):
                proxima = linhas[j].strip()
                if any(
                    proxima.startswith(k)
                    for k in ["Créditos:", "Carga Horária:", "Turma:", "Ano/Semestre:", "Horário:"]
                ):
                    break
                partes.append(proxima)
                j += 1

            nome_disciplina = " ".join(partes).strip()
            break

    return nome_disciplina


def encontrar_tabela_frequencia(pdf):
    for pagina in pdf.pages:
        texto = pagina.extract_text() or ""

        if "Lista de Freq" in texto or "Lista de Frequ" in texto:
            tabela = pagina.extract_table()
            if tabela and len(tabela) > 2:
                return tabela

            tabelas = pagina.extract_tables()
            if tabelas:
                tabela_maior = max(tabelas, key=lambda t: len(t) if t else 0)
                if tabela_maior and len(tabela_maior) > 2:
                    return tabela_maior

    return None


def mapear_colunas_meses(linha_meses):
    meses_colunas = {}
    mes_atual = None

    for i, celula in enumerate(linha_meses):
        valor = str(celula).strip() if celula else ""

        if valor:
            mes_atual = valor.lower()

        if mes_atual:
            if mes_atual not in meses_colunas:
                meses_colunas[mes_atual] = []
            meses_colunas[mes_atual].append(i)

    return meses_colunas


def limpar_nome(nome):
    return str(nome).replace("\n", " ").strip()


def celula_tem_registro(valor):
    """
    Retorna True se a aula foi dada para aquele aluno, isto é,
    existe algum registro na célula.
    """
    if valor is None:
        return False

    v = str(valor).strip().upper()
    return v != ""


def aluno_faltou_no_dia(valor):
    """
    Regras:
    - número > 0 => faltou naquele dia
    - *         => presente
    - J         => justificada, não conta como falta
    - vazio     => sem registro, não conta aula dada
    """
    if valor is None:
        return False

    v = str(valor).strip().upper()

    if not v:
        return False

    if v.isdigit() and int(v) > 0:
        return True

    return False


def calcular_percentual_presenca(total_aulas, dias_faltados):
    if total_aulas <= 0:
        return 0.0

    if dias_faltados >= total_aulas:
        return 0.0

    presencas = total_aulas - dias_faltados
    return round((presencas / total_aulas) * 100, 2)


def analisar_frequencia_por_mes(caminho_pdf):
    with pdfplumber.open(caminho_pdf) as pdf:
        nome_disciplina = extrair_nome_disciplina(pdf)
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

            matricula = str(linha[0]).strip() if linha[0] else ""
            nome = limpar_nome(linha[1]) if len(linha) > 1 else ""

            if not matricula.isdigit() or len(matricula) < 5:
                continue

            registro = {
                "Disciplina": nome_disciplina,
                "Matrícula": matricula,
                "Nome": nome
            }

            total_aulas_geral = 0
            total_dias_faltados_geral = 0

            for mes, colunas in meses_colunas.items():
                total_aulas_mes = 0
                dias_faltados_mes = 0

                for idx in colunas:
                    if idx >= len(linha):
                        continue

                    dia = ""
                    if idx < len(linha_dias) and linha_dias[idx] is not None:
                        dia = str(linha_dias[idx]).strip()

                    if not dia or not dia.isdigit():
                        continue

                    valor = linha[idx]

                    # Só conta aula dada se houver algum registro na célula
                    if not celula_tem_registro(valor):
                        continue

                    total_aulas_mes += 1

                    if aluno_faltou_no_dia(valor):
                        dias_faltados_mes += 1

                percentual_presenca_mes = calcular_percentual_presenca(
                    total_aulas_mes, dias_faltados_mes
                )

                registro[f"{mes.capitalize()}_Total_Aulas"] = total_aulas_mes
                registro[f"{mes.capitalize()}_Dias_Faltados"] = dias_faltados_mes
                registro[f"{mes.capitalize()}_%_Presença"] = percentual_presenca_mes

                total_aulas_geral += total_aulas_mes
                total_dias_faltados_geral += dias_faltados_mes

            percentual_presenca_geral = calcular_percentual_presenca(
                total_aulas_geral, total_dias_faltados_geral
            )

            registro["Total_Aulas_Geral"] = total_aulas_geral
            registro["Total_Dias_Faltados_Geral"] = total_dias_faltados_geral
            registro["%_Presença_Geral"] = percentual_presenca_geral

            resultados.append(registro)

        if not resultados:
            return None

        return pd.DataFrame(resultados)


def organizar_colunas_frequencia(df_final):
    colunas_fixas = ["Disciplina", "Matrícula", "Nome"]

    ordem_meses = [
        "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
        "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro", "Janeiro"
    ]

    colunas_ordenadas_meses = []
    for mes in ordem_meses:
        aula_col = f"{mes}_Total_Aulas"
        falta_col = f"{mes}_Dias_Faltados"
        presenca_col = f"{mes}_%_Presença"

        if aula_col in df_final.columns:
            colunas_ordenadas_meses.append(aula_col)
        if falta_col in df_final.columns:
            colunas_ordenadas_meses.append(falta_col)
        if presenca_col in df_final.columns:
            colunas_ordenadas_meses.append(presenca_col)

    colunas_finais = (
        colunas_fixas
        + colunas_ordenadas_meses
        + ["Total_Aulas_Geral", "Total_Dias_Faltados_Geral", "%_Presença_Geral"]
    )

    colunas_existentes = [c for c in colunas_finais if c in df_final.columns]
    return df_final[colunas_existentes]


# =========================================================
# CORS
# =========================================================

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


# =========================================================
# ROTAS
# =========================================================

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok",
        "message": "API de análise de faltas no ar - v2."
    })


@app.route("/analyze", methods=["POST", "OPTIONS"])
def analyze():
    if request.method == "OPTIONS":
        return "", 200

    mes_analise = request.form.get("mes", "Março")
    arquivos_enviados = request.files.getlist("arquivos")

    if not arquivos_enviados:
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    lista_dfs = []

    with tempfile.TemporaryDirectory() as tmpdirname:
        for f in arquivos_enviados:
            if not f or f.filename == "":
                continue

            filename = secure_filename(f.filename)
            filepath = os.path.join(tmpdirname, filename)
            f.save(filepath)

            try:
                df_temp = analisar_faltas_detalhado(filepath, mes_analise)
                if df_temp is not None and not df_temp.empty:
                    lista_dfs.append(df_temp)
            except Exception as e:
                print(f"Erro ao processar PDF {filename}: {e}")

    if lista_dfs:
        df_final = pd.concat(lista_dfs, ignore_index=True)

        # Mantém separado por disciplina
        df_final = df_final.groupby(
            ["Nome", "Matrícula", "Disciplina"],
            as_index=False
        ).agg({
            "Total Faltas (Mês)": "sum",
            "Datas das Faltas": lambda x: " // ".join(
                [str(v) for v in x if str(v).strip()]
            )
        })

        df_final = df_final.sort_values(by=["Nome", "Disciplina"])

        return jsonify(df_final.to_dict(orient="records"))

    return jsonify([])


@app.route("/analyze-frequency", methods=["POST", "OPTIONS"])
def analyze_frequency():
    if request.method == "OPTIONS":
        return "", 200

    arquivos_enviados = request.files.getlist("arquivos")

    if not arquivos_enviados:
        return jsonify({"erro": "Nenhum arquivo enviado."}), 400

    lista_dfs = []

    with tempfile.TemporaryDirectory() as tmpdirname:
        for f in arquivos_enviados:
            if not f or f.filename == "":
                continue

            filename = secure_filename(f.filename)
            filepath = os.path.join(tmpdirname, filename)
            f.save(filepath)

            try:
                df_temp = analisar_frequencia_por_mes(filepath)
                if df_temp is not None and not df_temp.empty:
                    lista_dfs.append(df_temp)
            except Exception as e:
                print(f"Erro ao processar PDF {filename}: {e}")

    if not lista_dfs:
        return jsonify([])

    df_final = pd.concat(lista_dfs, ignore_index=True)

    df_final = organizar_colunas_frequencia(df_final)
    df_final = df_final.sort_values(by=["Nome", "Disciplina"]).reset_index(drop=True)

    # Trata NaN para JSON válido
    df_final = df_final.fillna("")

    return jsonify(df_final.to_dict(orient="records"))


if __name__ == "__main__":
    app.run(debug=True)