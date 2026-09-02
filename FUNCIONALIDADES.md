# presente.edu — Funcionalidades Implementadas

Ferramenta gratuita para docentes e coordenações de curso do IFFar transformarem
os diários de classe exportados do **SIGAA** (PDF) em indicadores visuais de
frequência e alertas de evasão.

> Status: **versão beta em testes**.

---

## Visão geral da arquitetura

| Camada | Tecnologia | Arquivo |
|---|---|---|
| Landing page | HTML + Tailwind (CDN) | `index.html` |
| Aplicação (SPA) | HTML + Tailwind + Chart.js + jsPDF | `presente.html` |
| API de extração | Python / Flask + pdfplumber + pandas | `api/app.py` |
| Deploy da API | Vercel (`https://conta-faltas.vercel.app`) | — |

O front-end consome a API via `fetch`. O processamento pesado dos PDFs acontece
no servidor; a renderização, filtros e geração de relatórios acontecem no
navegador.

---

## 1. Landing page (`index.html`)

- Apresentação do produto com seções de funcionalidades, recursos, "como
  funciona", vídeo de demonstração (YouTube incorporado) e "sobre o projeto".
- Seção "Sobre o Projeto" com o autor (docente do IFFar desde 2012), formação
  acadêmica, propósito do projeto e links (LinkedIn, Lattes, contato).
- Banner de aviso de fase de testes e badge "versão beta".
- Slider automático de mockups do dashboard.
- Botão de acesso direto à ferramenta (`presente.html`) e link para formulário
  de feedback.
- Identidade visual institucional (verde IFFar `#32a041`).

---

## 2. API de extração de diários (`api/app.py`)

### Extração de metadados do PDF
- Leitura da página 1 do diário: **Centro, Curso, Coordenador do Curso, Código,
  Disciplina, Carga Horária, Ano/Semestre, Docente e Matrícula do Docente**.
- Tratamento robusto de PDFs "quebrados" pelo pdfplumber:
  - rótulos multilinha (`extrair_valor_rotulo_multilinha`);
  - texto de mês extraído na vertical, letra por letra
    (`normalizar_texto_mes`);
  - normalização de acentos para comparação (`sem_acento`);
  - casos em que o rótulo "Coordenador de Curso" é dividido em várias linhas.

### Inferência de peso da disciplina (períodos por aula)
- `36h` → 2 períodos (automático);
- `72h` → 4 períodos (sugestão, exige confirmação do usuário);
- outros → fallback de 2 períodos;
- o front-end pode enviar um mapa explícito `código → períodos` que tem
  prioridade sobre a inferência.

### Endpoints

| Método/Rota | Função |
|---|---|
| `GET /` | Health-check da API |
| `POST /check-disciplines` | **Etapa 1** — pré-análise: retorna metadados de cada PDF, deduplica disciplinas por código e marca `requer_confirmacao` quando a carga horária é ambígua (72h) |
| `POST /analyze` | **Etapa 2A** — análise de evasão de um mês específico: identifica alunos com faltas consecutivas no fim do mês, soma os períodos reais faltados e agrega o resultado por aluno/disciplina |
| `POST /analyze-frequency` | **Etapa 2B** — análise completa de frequência: percentual de presença por mês e geral, total de aulas e de dias faltados por aluno |

### Regras de contagem
- Marcador `J` (falta justificada) conta como **presença**.
- Faltas contam o valor real em períodos (2 ou 4), não o número literal da
  célula.
- Presença (`*`) e falta contam a carga de períodos da aula (à noite, 4).
- Colunas são mapeadas para o mês correto mesmo com cabeçalhos verticais.
- Upload de **múltiplos PDFs** processados em lote; disciplinas repetidas são
  deduplicadas.
- CORS liberado para consumo pelo front-end.

---

## 3. Aplicação de análise (`presente.html`)

### Fluxo de uso
1. Upload múltiplo de PDFs (`.pdf`) do diário SIGAA.
2. Se houver disciplina de 72h, abre **modal de confirmação de períodos**
   (2 ou 4 por aula) por código de disciplina.
3. Processa e exibe os resultados em duas abas.

### Aba "Frequência"
- Tabela com presença por mês (Fevereiro → Janeiro) e percentual geral por aluno.
- Células coloridas conforme o **limiar crítico de 75%** (verde acima /
  vermelho abaixo).
- Ordenação por total de faltas ou por percentual geral.
- Busca textual e filtro por disciplina.
- Paginação (35 itens por página) com navegação e atalhos para primeira/última
  página.

### Aba "Evasão"
- Seleção do mês a analisar.
- Lista de alunos em situação crítica (faltas consecutivas), com as datas das
  faltas e a disciplina.

### Painel de indicadores (KPIs)
- Total de alunos.
- Frequência média.
- Número de alertas de evasão.
- Card lateral com resumo por disciplina (quantidade e filtro rápido).

### Gráficos (Chart.js)
- **Gráfico individual do aluno**: série temporal de % de presença por mês, com
  faixas de fundo coloridas por faixa de risco e linha do limiar de 75%
  (plugin `monthBandPlugin` customizado).
- **Gráfico geral**: uma linha por aluno, eixo X = meses.
  - Filtro por disciplina.
  - Filtro por faixa de percentual com **slider duplo** (mín/máx).
  - Filtros rápidos "acima de 75%" / "abaixo de 75%".
  - Legenda interativa: ligar/desligar alunos individualmente, mostrar todos,
    ocultar todos.
  - Contador de alunos exibidos.

### Relatórios em PDF (jsPDF)
- PDF da tabela atual (respeitando filtros aplicados).
- PDF de detalhamento mensal por disciplina.
- PDF de frequência geral.
- **PDF consolidado por aluno**: dados do aluno, disciplinas, série de
  frequência e gráfico.
- Cabeçalho institucional com logo do IFFar (`iffar-horizontal.png`),
  convertido para base64 na geração.

### Privacidade
- Processamento sob demanda; nenhum dado de aluno é armazenado — os PDFs são
  gravados em diretório temporário no servidor e descartados ao fim da
  requisição (`tempfile.TemporaryDirectory`).

---

## Como rodar localmente

### API
```bash
cd api
python -m venv venv && source venv/bin/activate
pip install -r ../requirements.txt
python app.py           # sobe em http://127.0.0.1:5001
```

### Front-end
Abrir `index.html` / `presente.html` em um servidor estático (ex.: XAMPP ou
`python -m http.server`). Em `presente.html`, alterne a constante `API_URL`
para `http://127.0.0.1:5001` durante o desenvolvimento local.

---

## Dependências (`requirements.txt`)

`flask`, `flask-cors`, `pandas`, `pdfplumber`, `werkzeug`
