import os
import pandas as pd
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__)

# Habilita CORS para permitir requisições do seu domínio do GitHub Pages
CORS(app)

# --- NOTA: Insira aqui sua função real de extração (pdfplumber, etc) ---
def analisar_faltas_detalhado(caminho_pdf, mes):
    # Exemplo mockado (substitua pelo seu código original)
    data = {
        'Nome': ['João Silva', 'Maria Santos'],
        'Matrícula': ['123', '456'],
        'Disciplina': ['Cálculo', 'Física'],
        'Total Faltas (Mês)': [5, 8],
        'Datas das Faltas': ['05/03, 12/03', '01/03, 08/03']
    }
    return pd.DataFrame(data)

@app.route('/analyze', methods=['POST'])
def analyze():
    mes_analise = request.form.get('mes', 'Março')
    arquivos_enviados = request.files.getlist('arquivos')
    
    lista_dfs = []
    
    with tempfile.TemporaryDirectory() as tmpdirname:
        for f in arquivos_enviados:
            if f.filename == '': continue
            
            filename = secure_filename(f.filename)
            filepath = os.path.join(tmpdirname, filename)
            f.save(filepath)
            
            try:
                df_temp = analisar_faltas_detalhado(filepath, mes_analise)
                if df_temp is not None and not df_temp.empty:
                    lista_dfs.append(df_temp)
            except Exception as e:
                print(f"Erro ao processar {filename}: {e}")

    if lista_dfs:
        df_final = pd.concat(lista_dfs, ignore_index=True)
        
        df_agrupado = df_final.groupby(['Nome', 'Matrícula']).agg({
            'Disciplina': lambda x: ' | '.join(set(x)),
            'Total Faltas (Mês)': 'sum',
            'Datas das Faltas': lambda x: ' // '.join(map(str, x))
        }).reset_index()

        df_agrupado = df_agrupado.sort_values(by='Nome')
        return jsonify(df_agrupado.to_dict(orient='records'))
    
    return jsonify([])
