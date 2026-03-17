import os
import pandas as pd
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename
import tempfile

app = Flask(__name__)

# Configuração de CORS para permitir acesso do GitHub Pages (ou qualquer origem)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- FUNÇÃO DE ANÁLISE (Substitua pela sua lógica real) ---
def analisar_faltas_detalhado(caminho_pdf, mes):
    """
    Aqui entra o seu código original que lê o PDF.
    Este é apenas um exemplo de retorno.
    """
    # Exemplo de dados para teste:
    data = {
        'Nome': ['João Silva', 'Maria Santos', 'Ana Oliveira', 'Bruno Souza'],
        'Matrícula': ['20261001', '20261002', '20261003', '20261004'],
        'Disciplina': ['Álgebra Linear', 'Desenvolvimento Web', 'Cálculo I', 'Física II'],
        'Total Faltas (Mês)': [12, 8, 15, 6],
        'Datas das Faltas': ['05/03, 12/03, 19/03', '01/03, 08/03', '02/03, 09/03, 16/03', '10/03']
    }
    return pd.DataFrame(data)

# --- INTERFACE HTML/JS ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Coordenadoria - Análise de Evasão</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 min-h-screen font-sans text-slate-900">
    <div class="max-w-5xl mx-auto py-10 px-4">
        <header class="mb-10 text-center">
            <h1 class="text-3xl font-bold text-blue-900 tracking-tight">Relatório de Evasão Consolidado</h1>
            <p class="text-slate-500 mt-2">Upload de diários de classe para análise automática de faltas.</p>
        </header>

        <div class="bg-white p-6 rounded-2xl shadow-sm mb-8 border border-slate-200">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6 items-end">
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Mês de Análise</label>
                    <select id="mes_analise" class="w-full border-slate-200 rounded-xl shadow-sm focus:border-blue-500 focus:ring-blue-500 p-2.5 border bg-white text-black">
                        <option value="Janeiro">Janeiro</option>
                        <option value="Fevereiro">Fevereiro</option>
                        <option value="Março" selected>Março</option>
                        <option value="Abril">Abril</option>
                        <option value="Maio">Maio</option>
                        <option value="Junho">Junho</option>
                    </select>
                </div>
                <div class="md:col-span-1">
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Diários (PDF)</label>
                    <input type="file" id="arquivos" multiple accept=".pdf" class="w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 cursor-pointer">
                </div>
                <button onclick="processarPDFs()" id="btn-processar" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 px-6 rounded-xl transition shadow-md active:scale-95">
                    Processar Agora
                </button>
            </div>
        </div>

        <div id="loading" class="hidden text-center py-12">
            <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
            <p class="mt-4 text-slate-600 font-medium tracking-tight">Analisando PDFs e consolidando dados...</p>
        </div>

        <div id="resultado" class="hidden space-y-4">
            <!-- Filtro de Busca -->
            <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div class="relative flex-1">
                    <span class="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-400">
                        <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
                    </span>
                    <input type="text" id="filtro_nome" onkeyup="filtrarTabela()" placeholder="Buscar aluno por nome..." class="pl-10 w-full border-slate-200 rounded-lg p-2 text-sm focus:ring-blue-500 focus:border-blue-500 border text-black">
                </div>
                <div class="text-xs text-slate-400 font-medium">
                    Ordenado por Nome (A-Z)
                </div>
            </div>

            <!-- Tabela -->
            <div class="bg-white rounded-2xl shadow-sm overflow-hidden border border-slate-200">
                <div class="p-5 border-b bg-slate-50 flex justify-between items-center">
                    <h2 class="font-bold text-slate-700 text-sm uppercase tracking-widest text-black">Alunos em padrão de evasão</h2>
                    <span id="label-mes" class="bg-blue-100 text-blue-700 text-xs font-black px-3 py-1 rounded-full uppercase">Março</span>
                </div>
                <div class="overflow-x-auto text-black">
                    <table class="min-w-full divide-y divide-slate-200">
                        <thead class="bg-slate-50">
                            <tr class="text-left text-[10px] font-black text-slate-400 uppercase tracking-widest">
                                <th class="px-6 py-4">Nome</th>
                                <th class="px-6 py-4">Matrícula</th>
                                <th class="px-6 py-4">Disciplinas</th>
                                <th class="px-6 py-4 text-center">Total Faltas</th>
                                <th class="px-6 py-4">Datas</th>
                            </tr>
                        </thead>
                        <tbody id="tabela-corpo" class="bg-white divide-y divide-slate-100 text-sm">
                            <!-- Conteúdo injetado pelo JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        
        <div id="vazio" class="hidden text-center py-24 text-slate-400 italic">
            Nenhum aluno em padrão de evasão detectado para os critérios selecionados.
        </div>
    </div>

    <script>
        const API_URL = "https://conta-faltas.vercel.app"; 
        let dadosOriginais = []; // Armazena o resultado da API

        async function processarPDFs() {
            const fileInput = document.getElementById('arquivos');
            const mes = document.getElementById('mes_analise').value;
            const btn = document.getElementById('btn-processar');
            const loader = document.getElementById('loading');
            const resDiv = document.getElementById('resultado');
            const vazioDiv = document.getElementById('vazio');
            
            if (fileInput.files.length === 0) {
                alert("Selecione ao menos um PDF para continuar.");
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
                const cleanUrl = API_URL.replace(/\/+$/, "");
                const response = await fetch(cleanUrl + "/analyze", {
                    method: 'POST',
                    body: formData,
                    mode: 'cors'
                });
                
                if (!response.ok) throw new Error("Erro no servidor: " + response.status);
                
                const data = await response.json();
                
                if (data && data.length > 0) {
                    // Salva e ordena por nome antes de exibir
                    dadosOriginais = data.sort((a, b) => a.Nome.localeCompare(b.Nome));
                    renderizarTabela(dadosOriginais, mes);
                    resDiv.classList.remove('hidden');
                } else {
                    vazioDiv.classList.remove('hidden');
                }
            } catch (error) {
                console.error("Erro:", error);
                alert("Não foi possível conectar à API.");
            } finally {
                btn.disabled = false;
                loader.classList.add('hidden');
            }
        }

        function filtrarTabela() {
            const termo = document.getElementById('filtro_nome').value.toLowerCase();
            const mes = document.getElementById('mes_analise').value;
            
            const dadosFiltrados = dadosOriginais.filter(aluno => 
                aluno.Nome.toLowerCase().includes(termo) || 
                aluno.Matrícula.toLowerCase().includes(termo)
            );
            
            renderizarTabela(dadosFiltrados, mes);
        }

        function renderizarTabela(data, mes) {
            const corpo = document.getElementById('tabela-corpo');
            document.getElementById('label-mes').innerText = mes;
            corpo.innerHTML = '';

            if (data.length === 0) {
                corpo.innerHTML = '<tr><td colspan="5" class="px-6 py-10 text-center text-slate-400 italic">Nenhum resultado encontrado para a busca.</td></tr>';
                return;
            }

            data.forEach(function(aluno) {
                const tr = document.createElement('tr');
                tr.className = "hover:bg-slate-50 transition-colors";
                
                let html = '<td class="px-6 py-4 font-bold text-slate-800">' + aluno.Nome + '</td>';
                html += '<td class="px-6 py-4 font-mono text-xs text-slate-500">' + aluno.Matrícula + '</td>';
                html += '<td class="px-6 py-4 text-slate-600 text-xs">' + aluno.Disciplina + '</td>';
                html += '<td class="px-6 py-4 text-center font-black text-red-600 font-mono text-lg">' + aluno['Total Faltas (Mês)'] + '</td>';
                html += '<td class="px-6 py-4 text-[10px] text-slate-400 italic">' + aluno['Datas das Faltas'] + '</td>';
                
                tr.innerHTML = html;
                corpo.appendChild(tr);
            });
        }
    </script>
</body>
</html>
"""

# --- ROTAS FLASK ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/analyze', methods=['POST', 'OPTIONS'])
def analyze():
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
                print("Erro ao processar PDF:", e)

    if lista_dfs:
        df_final = pd.concat(lista_dfs, ignore_index=True)
        df_agrupado = df_final.groupby(['Nome', 'Matrícula']).agg({
            'Disciplina': lambda x: ' | '.join(set(x)),
            'Total Faltas (Mês)': 'sum',
            'Datas das Faltas': lambda x: ' // '.join(map(str, x))
        }).reset_index()

        # O Backend envia os dados, o Frontend cuida da ordenação alfabética
        return jsonify(df_agrupado.to_dict(orient='records'))
    
    return jsonify([])

if __name__ == '__main__':
    app.run(debug=True)
