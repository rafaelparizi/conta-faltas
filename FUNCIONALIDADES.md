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
| Autenticação | HTML + Tailwind (CDN) | `auth.html` |
| Aplicação (SPA) | HTML + Tailwind + Chart.js + jsPDF | `presente.html` |
| API de extração | Python / Flask + pdfplumber + pandas | `api/app.py` |
| Deploy da API | Vercel (`https://conta-faltas.vercel.app`) | — |

O front-end consome a API via `fetch`. O processamento pesado dos PDFs acontece
no servidor; a renderização, filtros e geração de relatórios acontecem no
navegador.

---

## 0. Autenticação (`auth.html`)

> **Versão beta:** ainda **sem OAuth real**. O botão apenas registra o acesso
> localmente e encaminha para a ferramenta. A integração com o Google Identity
> Services (e a restrição ao domínio `@iffar.edu.br`) fica para uma próxima versão.

- Página de login com identidade visual da landing (logo, verde IFFar, fonte Inter).
- Único controle: botão **"Entrar com Google"** (ícone oficial em SVG).
- Fluxo: `index.html` (landing) → `auth.html` (login) → `presente.html` (ferramenta).
- Ao clicar no botão, grava `presente_auth = '1'` e `presente_auth_ts` no
  `localStorage` e redireciona para `presente.html`.
- Se o usuário já tiver a flag, `auth.html` pula direto para `presente.html`.
- `presente.html` é protegido: sem a flag `presente_auth`, redireciona de volta
  para `auth.html`.
- Botão **"Sair"** no topo do `presente.html`: limpa a flag do `localStorage` e
  volta para `auth.html`.
- Ainda não há tela de perfil nem proteção da API Flask (segue aberta).

---

## 1. Landing page (`index.html`)

- Apresentação do produto com seções de funcionalidades, recursos, "como
  funciona", vídeo de demonstração (YouTube incorporado) e "sobre o projeto".
- Seção "Sobre o Projeto" com o autor (docente do IFFar desde 2012), formação
  acadêmica, propósito do projeto e links (LinkedIn, Lattes, contato).
- Banner de aviso de fase de testes e badge "versão beta".
- Slider automático de mockups do dashboard.
- Botão de acesso à ferramenta, que agora passa pela página de login
  (`auth.html` → `presente.html`), e link para formulário de feedback.
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

### Inferência de peso da disciplina (períodos por dia de aula)
O "peso" é o número de períodos de cada dia de aula (cada coluna do diário) e é
o que transforma faltas em porcentagem: `% = (aulas − faltas) / aulas`.

Sugestão inicial pela carga horária semestral (`MAPA_CH_PERIODOS`):

| Nível | CH | períodos/dia |
|---|---|---|
| Superior | `36h` / `72h` | 2 / 4 |
| Técnico/Integrado | `40h` / `80h` / `120h` | 1 / 2 / 3 |
| — | outra CH | fallback 2 |

- O **nível** (`integrado` / `superior`) é apenas rótulo/sugestão — não altera o
  cálculo. `nivel_sugerido_por_ch` deriva pelo mesmo conjunto de CHs.
- O front-end **sempre** exibe a tela de configuração e envia um mapa explícito
  `código → períodos` (`pesos`) que tem prioridade sobre a inferência. O usuário
  pode ajustar quando a disciplina tem aula em mais de um dia por semana.

### Endpoints

| Método/Rota | Função |
|---|---|
| `GET /` | Health-check da API |
| `POST /check-disciplines` | **Etapa 1** — pré-análise: retorna metadados de cada PDF (deduplicados por código) com `peso_sugerido` e `nivel_sugerido` pela CH; `requer_confirmacao` sinaliza CH que pode ser distribuída em mais de um dia (72/80/120h) |
| `POST /analyze` | **Etapa 2A** — análise de evasão de um mês específico: identifica alunos com faltas consecutivas no fim do mês, soma os períodos reais faltados e agrega o resultado por aluno/disciplina |
| `POST /analyze-frequency` | **Etapa 2B** — análise completa de frequência: percentual de presença por mês e geral, total de aulas e de dias faltados por aluno |
| `POST /analyze-historico` | **Avaliação individual** — recebe um PDF de Histórico Escolar (campo `arquivo`) e devolve o status atualizado de um aluno (ver abaixo) |

### Parser de Histórico Escolar (avaliação individual do aluno)
Portado de `teste_parser/parser.py` (documentação completa em
`teste_parser/DOCUMENTACAO.md`). Lê o PDF de "Histórico Escolar" do SIGAA e monta,
para **um** aluno, um resumo pronto para virar JSON de API:

- **Página 1** (texto): nome, matrícula, curso, status, período atual, prazo
  máximo, MC e IRA. O nome vem com caracteres duplicados por artefato de negrito
  do PDF — corrigido por `_dedupe_bold_artifact` só quando o padrão bate em todos
  os tokens (não corrompe letras dobradas legítimas).
- **Tabelas** (páginas 2+): componentes cursados/cursando (11 colunas, parse
  posicional) e a lista de obrigatórias pendentes (pode atravessar a quebra de
  página; os pedaços são somados). O **docente** de cada componente é extraído da
  2ª linha da célula do nome (removido o sufixo de CH); nas pendentes que o aluno
  já cursou/está cursando, o docente é herdado por código.
- Classificação de aprovação/reprovação usa **exclusivamente a coluna
  "Situação"** (`APR`, `REP`, `REPF`, `REPMF`, `MATR`, `DISP`, `CUMP`, `CANC`,
  `TRANC`) — nunca a nota.
- Retorno de `resumo_status`:
  - identificação: `aluno`, `matricula`, `curso`, `status_matricula`,
    `periodo_ingresso`, `forma_ingresso`, `periodo_atual`, `prazo_padrao`,
    `prazo_maximo`, `indices` (MC, IRA);
  - `componentes_por_situacao`, `resumo` (aprovados, reprovados por falta/média,
    em curso, pendentes);
  - `carga_horaria`: `concluida` / `em_curso` / `pendente` (soma da CH das
    obrigatórias pendentes) e `pct_conclusao_estimado` = concluída ÷ (concluída +
    pendente). **Estimativa** — a CH total do currículo não é extraída do PDF;
  - `percentuais` (reprovação por falta/média sobre a base de avaliados nesta
    oferta; REPMF conta nos dois);
  - listas `disciplinas_aprovadas` (com `carga_horaria` e `media`),
    `disciplinas_a_cursar` (com `matriculado_atualmente`), `reprovacoes_detalhe`
    (com `carga_horaria`, `media`, `freq_pct`, `situacao`);
  - `desempenho_por_semestre`: por período letivo, contagem de componentes /
    aprovados / reprovados / em curso, `media_semestre` (média dos avaliados) e
    `pct_aprovacao` / `pct_reprovacao` (sobre aprovados + reprovados do semestre).
- Funções: `parse_historico(pdf)` → `HistoricoAluno`; `resumo_status(h)` → `dict`.
  Validado contra os 2 históricos de teste.

### Regras de contagem
- Marcador `J` (falta justificada) conta como **presença**.
- Cada dia de aula (coluna) vale `peso_disciplina` períodos, tanto para presença
  (`*`) quanto para falta — não se usa o número literal da célula.
- Faltas registram `peso_disciplina` períodos por dia faltado.
- Colunas são mapeadas para o mês correto mesmo com cabeçalhos verticais.
- Upload de **múltiplos PDFs** processados em lote; disciplinas repetidas são
  deduplicadas.
- CORS liberado para consumo pelo front-end.

---

## 3. Aplicação de análise (`presente.html`)

### Layout
- Interface em painel: **barra lateral fixa à esquerda** (marca, navegação entre
  as análises, seletor de mês, upload dos PDFs, **indicador de carregamento**,
  botão "Sair" e crédito) e **área de conteúdo à direita** com título dinâmico,
  ação de PDF, cartões de indicadores, resumo por disciplina e o conteúdo em abas.
- A **animação de carregamento** aparece na barra lateral, logo abaixo do botão
  "Processar relatório".
- O **resumo "Disciplinas × alunos"** fica acima da tabela (antes do conteúdo em
  abas), em **cards compactos** dispostos em grade de até 4 por linha, com filtro
  rápido por disciplina.
- **Duas abas de conteúdo** (a de gráfico é a **ativa por padrão**):
  1. **Gráfico da turma** — o gráfico geral (uma linha por aluno) embutido na
     página, com seus filtros e legenda; desabilitada na análise "Busca por mês".
  2. **Planilha de alunos** — a tabela detalhada por aluno/mês com busca e
     paginação.
- Responsivo: em telas estreitas a barra lateral vira um bloco no topo.

### Modos (navegação da barra lateral)
- **Frequência geral** / **Busca por mês** — análise dos diários de classe
  (comportamento descrito abaixo).
- **Avaliação aluno (individual)** — troca o rótulo do upload para "Selecionar
  histórico do aluno" e o botão para "Avaliar aluno". Envia o PDF do histórico
  para `POST /analyze-historico` e mostra o resultado em **duas abas**:
  - **Visão geral** — cards reconstruindo o histórico do aluno, nesta ordem:
    (1) **dados do aluno** (matrícula e ano de ingresso em destaque) ao lado do
    (2) **progresso do curso** (% de conclusão estimado + CH faltante com barra);
    (3) **desempenho por semestre** (gráfico Chart.js: linha da média + barras de
    aprovados/reprovados); (4) **disciplinas aprovadas** e **a cursar** lado a
    lado; (5) **disciplinas reprovadas** abaixo, em largura total. Cada card de
    disciplinas mostra o **docente** e tem **campo de busca** (filtra por código,
    componente ou docente, com contador "N de N"). Disciplinas em que o aluno
    está matriculado agora recebem o badge "Matriculado". No canto inferior
    esquerdo do card de progresso há o botão **"Gerar relatório PDF"** (jsPDF +
    jspdf-autotable): cabeçalho institucional, dados do aluno, progresso,
    tabela de desempenho por semestre (com % de aprovação/reprovação) **+ o
    próprio gráfico**, e as três tabelas de disciplinas (com docente). O arquivo
    sai como `avaliacao_<nome-do-aluno>_<matrícula>.pdf`.
  - **JSON** — retorno bruto da API com botão "Copiar JSON".
  Trocar de modo limpa o arquivo selecionado.

### Fluxo de uso
1. Upload múltiplo de PDFs (`.pdf`) do diário SIGAA pela barra lateral.
2. Abre a **tela de configuração de disciplinas** listando todas as disciplinas:
   por disciplina, escolhe-se o **nível** (Integrado / Superior) e os **períodos
   por dia de aula** (1 a 4), pré-preenchidos pela sugestão da carga horária.
   Trocar o nível re-sugere os períodos/dia.
3. Processa e exibe os resultados; a navegação da barra lateral alterna entre as
   duas análises ("Frequência geral" e "Busca por mês") e as abas de conteúdo
   alternam entre gráfico e planilha.

### "Frequência geral"
- Tabela com presença por mês (Fevereiro → Janeiro) e percentual geral por aluno.
- Células coloridas conforme o **limiar crítico de 75%** (verde acima /
  vermelho abaixo).
- Ordenação por total de faltas ou por percentual geral.
- Busca textual e filtro por disciplina.
- Paginação (35 itens por página) com navegação e atalhos para primeira/última
  página.

### "Busca por mês"
- Seleção do mês a analisar (na barra lateral).
- Lista de alunos em situação crítica (faltas consecutivas), com as datas das
  faltas e a disciplina.

### Painel de indicadores (KPIs)
- Total de alunos.
- Frequência média.
- Número de alertas de evasão.
- Card com resumo por disciplina (quantidade, badge de nível e filtro rápido),
  acima da tabela.

### Gráficos (Chart.js)
- **Gráfico individual do aluno**: série temporal de % de presença por mês, com
  faixas de fundo coloridas por faixa de risco e linha do limiar de 75%
  (plugin `monthBandPlugin` customizado).
- **Gráfico da turma** (aba de conteúdo, não é mais modal): uma linha por aluno,
  eixo X = **apenas os meses presentes nos diários processados** (detectados no
  próprio front-end pelas colunas `<mês>_Total_Aulas` / `<mês>_%_Presença`; não
  exige mudança na API).
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
- **PDF da avaliação individual** (histórico): cabeçalho, dados do aluno,
  progresso do curso, desempenho por semestre e as tabelas de disciplinas
  aprovadas / a cursar / reprovadas (com docente) — usa `jspdf-autotable`.
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
