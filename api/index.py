import os
import pandas as pd
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__)

# Configuração de CORS robusta
# O parâmetro 'resources' garante que todas as rotas aceitem requisições externas
CORS(app, resources={r"/*": {"origins": "*"}})

# --- NOTA: Substitua esta função pela sua lógica real de extração do PDF ---
def analisar_faltas_detalhado(caminho_pdf, mes):
    """
    Sua lógica original de leitura de PDF deve ser colada aqui.
    """
    # Mock para teste
    data = {
        'Nome': ['João Silva', 'Maria Santos'],
        'Matrícula': ['123', '456'],
        'Disciplina': ['Cálculo', 'Física'],
        'Total Faltas (Mês)': [5, 8],
        'Datas das Faltas': ['05/03, 12/03', '01/03, 08/03']
    }
    return pd.DataFrame(data)

# --- TEMPLATE FRONT-END (HTML + CSS Tailwind + JS) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Coordenadoria - Análise de Evasão</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen font-sans text-slate-900">
    <div class="max-w-5xl mx-auto py-10 px-4">
        <header class="mb-10 text-center">
            <h1 class="text-3xl font-bold text-blue-800">Relatório de Evasão Consolidado</h1>
            <p class="text-gray-600 mt-2">Upload de diários de classe para análise de faltas por aluno.</p>
        </header>

        <div class="bg-white p-6 rounded-xl shadow-md mb-8 border border-slate-200">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6 items-end">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Mês de Análise</label>
                    <select id="mes_analise" class="w-full border-gray-300 rounded-lg shadow-sm focus:border-blue-500 focus:ring-blue-500 p-2 border">
                        <option value="Janeiro">Janeiro</option>
                        <option value="Fevereiro">Fevereiro</option>
                        <option value="Março" selected>Março</option>
                        <option value="Abril">Abril</option>
                        <option value="Maio">Maio</option>
                        <option value="Junho">Junho</option>
                    </select>
                </div>
                <div class="md:col-span-1">
                    <label class="block text-sm font-medium text-gray-700 mb-1">Selecionar PDFs</label>
                    <input type="file" id="arquivos" multiple accept=".pdf" class="w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100">
                </div>
                <button onclick="processarPDFs()" id="btn-processar" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-6 rounded-lg transition duration-200">
                    Processar Relatório
                </button>
            </div>
        </div>

        <div id="loading" class="hidden text-center py-10">
            <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
            <p class="mt-4 text-gray-600">Analisando documentos... isso pode levar alguns segundos.</p>
        </div>

        <div id="resultado" class="hidden bg-white rounded-xl shadow-md overflow-hidden border border-slate-200">
            <div class="p-4 border-b bg-gray-50 flex justify-between items-center">
                <h2 class="font-bold text-gray-700 text-sm uppercase tracking-wider">Alunos com padrão de evasão detectado</h2>
                <span id="label-mes" class="bg-blue-100 text-blue-800 text-xs font-semibold px-2.5 py-0.5 rounded">Março</span>
            </div>
            <div class="overflow-x-auto">
                <table class="min-w-full divide-y divide-gray-200">
                    <thead class="bg-gray-50 text-slate-500 font-bold uppercase text-xs">
                        <tr>
                            <th class="px-6 py-3 text-left">Nome</th>
                            <th class="px-6 py-3 text-left">Matrícula</th>
                            <th class="px-6 py-3 text-left">Disciplinas</th>
                            <th class="px-6 py-3 text-center">Faltas</th>
                            <th class="px-6 py-3 text-left">Datas</th>
                        </tr>
                    </thead>
                    <tbody id="tabela-corpo" class="bg-white divide-y divide-gray-200 text-sm">
                        <!-- JS content -->
                    </tbody>
                </table>
            </div>
        </div>
        
        <div id="vazio" class="hidden text-center py-20 text-gray-500 italic">
            Nenhum aluno em padrão de evasão detectado.
        </div>
    </div>

    <script>
        // --- CONFIGURAÇÃO ---
        const API_URL = "https://conta-faltas.vercel.app"; 

        async function processarPDFs() {
            const fileInput = document.getElementById('arquivos');
            const mes = document.getElementById('mes_analise').value;
            const btn = document.getElementById('btn-processar');
            const loader = document.getElementById('loading');
            const resDiv = document.getElementById('resultado');
            const vazioDiv = document.getElementById('vazio');
            
            if (fileInput.files.length === 0) {
                alert("Selecione os arquivos.");
                return;
            }

            const formData = new FormData();
            formData.append('mes', mes);
            for (let i = 0; i < fileInput.files.length; i++) {
                formData.append('arquivos', fileInput.files[i]);
            }

            btn.disabled = true;
            loader.classList.remove('hidden');
            resDiv.classList.add('hidden');
            vazioDiv.classList.add('hidden');

            try {
                // Remove barras extras e garante que o endpoint termina SEM barra para evitar o Redirect 308 do Vercel
                const baseUrl = API_URL.replace(/\/+$/, "");
                const endpoint = `\${baseUrl}/analyze`;
                
                console.log("Chamando:", endpoint);

                const response = await fetch(endpoint, {
                    method: 'POST',
                    body: formData,
                    mode: 'cors',
                    headers: {
                        'Accept': 'application/json'
                    }
                });
                
                if (!response.ok) {
                    const errorDetail = await response.text();
                    throw new Error(`Erro \${response.status}: \${errorDetail}`);
                }
                
                const data = await response.json();
                
                if (data && data.length > 0) {
                    renderizarTabela(data, mes);
                    resDiv.classList.remove('hidden');
                } else {
                    vazioDiv.classList.remove('hidden');
                }
            } catch (error) {
                console.error("Erro completo:", error);
                alert("Erro de conexão.\\n\\nVerifique se o Vercel concluiu o deploy e se o requirements.txt possui 'flask-cors'.");
            } finally {
                btn.disabled = false;
                loader.classList.add('hidden');
            }
        }

        function renderizarTabela(data, mes) {
            const corpo = document.getElementById('tabela-corpo');
            document.getElementById('label-mes').innerText = mes;
            corpo.innerHTML = '';
            data.forEach(aluno => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="px-6 py-4 font-semibold text-gray-900">\${aluno.Nome}</td>
                    <td class="px-6 py-4 font-mono text-xs text-gray-500">\${aluno.Matrícula}</td>
                    <td class="px-6 py-4 text-gray-600">\${aluno.Disciplina}</td>
                    <td class="px-6 py-4 text-center font-bold text-red-600">\${aluno['Total Faltas (Mês)']}</td>
                    <td class="px-6 py-4 text-xs text-gray-400 font-italic">\${aluno['Datas das Faltas']}</td>
                `;
                corpo.appendChild(tr);
            });
        }
    </script>
</body>
</html>
"""

# --- ROTAS DA API ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/analyze', methods=['POST', 'OPTIONS'])
def analyze():
    # OPTIONS é necessário para o 'preflight' do CORS em alguns casos
    if request.method == 'OPTIONS':
        return '', 200

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
                print(f"Erro processando {filename}: {e}")

    if lista_dfs:
        df_final = pd.concat(lista_dfs, ignore_index=True)
        df_agrupado = df_final.groupby(['Nome', 'Matrícula']).agg({
            'Disciplina': lambda x: ' | '.join(set(x)),
            'Total Faltas (Mês)': 'sum',
            'Datas das Faltas': lambda x: ' // '.join(map(str, x))
        }).reset_index()
        return jsonify(df_agrupado.sort_values(by='Nome').to_dict(orient='records'))
    
    return jsonify([])

if __name__ == '__main__':
    app.run(debug=True)
