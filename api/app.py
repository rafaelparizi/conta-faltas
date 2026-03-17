import os
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})


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


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok",
        "message": "API de análise de faltas no ar."
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


if __name__ == "__main__":
    app.run(debug=True)
