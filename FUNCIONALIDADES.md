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
| Autenticação | Firebase Authentication (Google) | `auth.html`, `auth-common.js`, `firebase-config.js` |
| Aprovação de acesso | HTML + Tailwind | `admin.html` |
| Aplicação (SPA) | HTML + Tailwind + Chart.js + jsPDF | `presente.html` |
| API de extração | Python / Flask + pdfplumber + pandas | `api/app.py` |
| Banco (acessos) | Firestore (via Firebase Admin SDK, só na API) | — |
| Deploy da API | Vercel (`https://conta-faltas.vercel.app`) | — |

O front-end consome a API via `fetch`. O processamento pesado dos PDFs acontece
no servidor; a renderização, filtros e geração de relatórios acontecem no
navegador.

---

## 0. Autenticação e acesso (`auth.html`, `admin.html`)

Login real com conta Google via **Firebase Authentication**, e acesso à
ferramenta restrito a **coordenadores aprovados**.

- Fluxo: `index.html` (landing) → `auth.html` (login) → `presente.html` (ferramenta).
- **Login**: botão "Entrar com Google" (`signInWithPopup`, persistência LOCAL).
  Depois do login, `auth.html` consulta `GET /auth/status` e mostra a tela
  conforme o status do e-mail:
  - **aprovado** → vai para `presente.html`;
  - **novo** → formulário "Solicitar acesso": nome, curso que coordena e
    **comprovante** (PDF/imagem, até 700KB), enviado para `POST /auth/solicitar`;
  - **pendente** → "Solicitação em análise";
  - **rejeitado** → "Acesso não aprovado".
- **Admin** (`ADMIN_EMAILS` em `api/app.py`, hoje só
  `rafael.parizi@iffarroupilha.edu.br`): sempre aprovado, sem depender do
  Firestore. Vê o link **"Solicitações de acesso"** no rodapé do menu, que
  abre `admin.html`: lista as solicitações pendentes, abre o comprovante num
  modal e tem os botões **Aprovar / Recusar** (`/admin/pendentes`,
  `/admin/comprovante/<email>`, `/admin/decidir`).
- **Notificações por e-mail** para quem pediu acesso, enviadas por SMTP com a
  conta do admin (`SMTP_USER` / `SMTP_PASSWORD`; `Reply-To` sempre
  `rafael.parizi@iffarroupilha.edu.br`):
  1. ao enviar o pedido — "solicitação recebida, em análise";
  2. ao aprovar — acesso aprovado, com o passo a passo para entrar (link do
     `auth.html`, "Entrar com Google" com a mesma conta, enviar os diários);
  3. ao recusar — não aprovado, com orientação para entrar em contato por e-mail.

  Falha no envio (ou SMTP não configurado) nunca bloqueia o pedido nem a
  decisão: a API devolve `email_enviado: false` e o `admin.html` avisa para
  contatar a pessoa manualmente. Variáveis opcionais: `SMTP_HOST` (padrão
  `smtp.gmail.com`), `SMTP_PORT` (padrão `587`; `465` usa SSL direto) e
  `APP_URL` (link de acesso nos e-mails).
- **Status do pedido**: sem registro em `coordenadores`, vale o status da
  solicitação (`pendente` / `rejeitado`) — quem já pediu vê "em análise" ou
  "não aprovado" ao entrar, e não o formulário de novo.
- **Fuso horário**: o Firestore guarda datas em UTC; a API devolve toda data
  no fuso de **São Paulo** (ISO 8601 com offset, ex.:
  `2026-09-26T11:40:25-03:00`) e o `admin.html` exibe com
  `timeZone: 'America/Sao_Paulo'`, independente do fuso do navegador.
- **Comprovante no Firestore**, em base64 dentro do documento da solicitação
  — sem Firebase Storage, que exige plano pago (Blaze) para ser habilitado.
- **A API também exige login**: todas as rotas de dados (`/check-disciplines`,
  `/analyze`, `/analyze-frequency`, `/analyze-historico`) exigem o ID token do
  Firebase no header `Authorization: Bearer ...` **e** e-mail aprovado. O
  front manda o token via `apiFetch()` (`auth-common.js`).
- **Todo acesso ao Firestore passa pela API** (Firebase Admin SDK). O front
  nunca fala direto com o Firestore — só com o Firebase Auth e com a API —,
  então as regras do Firestore podem negar todo acesso de cliente.
- `presente.html` fica escondido até confirmar login + aprovação; senão volta
  para `auth.html`. Mostra o e-mail logado e o botão **"Sair"** no rodapé do menu.
- **Proteções**: espera o Firebase restaurar a sessão salva (`authStateReady`)
  antes de decidir se há usuário logado; trava de loop de redirecionamento
  (mais de 5 redirecionamentos em 8s → para e mostra erro); timeout de 10s se
  o Firebase não responder; apaga a flag `presente_auth` do login falso da v1.
- **Configuração**: `firebase-config.js` tem a config pública do projeto
  (`sigaa-frequencia`). A API lê a chave da conta de serviço de
  `GOOGLE_APPLICATION_CREDENTIALS` (caminho de arquivo, usado no Docker local)
  ou `FIREBASE_SERVICE_ACCOUNT` (conteúdo JSON, para a Vercel). A chave
  (`firebase-service-account*.json`) está no `.gitignore`.

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
| `POST /analyze-historico` | **Análise individual** — recebe um PDF de Histórico Escolar (campo `arquivo`) e devolve o status atualizado de um aluno (ver abaixo) |

### Parser de Histórico Escolar (análise individual do aluno)
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
    `disciplinas_a_cursar` (obrigatórias pendentes **+** disciplinas eletivas em
    que o aluno está matriculado agora — situação `MATR` no histórico que não
    constam na lista de pendentes do PPC —, todas com `matriculado_atualmente`),
    `reprovacoes_detalhe`
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
- **Análise aluno (individual)** — troca o rótulo do upload para "Selecionar
  histórico do aluno" e o botão para "Analisar aluno". Envia o PDF do histórico
  para `POST /analyze-historico` e mostra o resultado em **três abas**:
  - **Visão geral** — cards reconstruindo o histórico do aluno, nesta ordem:
    (1) **dados do aluno** (matrícula e ano de ingresso em destaque), com o
    botão **"Gerar relatório PDF"** (jsPDF + jspdf-autotable) dentro do card:
    cabeçalho institucional, dados do aluno, progresso, tabela de desempenho
    por semestre (com % de aprovação/reprovação) **+ o próprio gráfico**, e as
    três tabelas de disciplinas (com docente). Arquivo
    `analise_<nome-do-aluno>_<matrícula>.pdf`; (2) **progresso do curso** (% de
    conclusão estimado + CH faltante com barra) e, logo abaixo na mesma coluna,
    (2b) **disciplinas do semestre corrente** (as com `matriculado_atualmente`,
    em pequenos cards numa grade que se ajusta à largura da coluna — evita que
    a lista fique desproporcionalmente mais alta que o card "Dados do aluno"
    ao lado);
    (3) **desempenho por semestre** (gráfico Chart.js: linha da média + barras
    de aprovados/reprovados); (4) **disciplinas aprovadas** e **a cursar** lado
    a lado; (5) **disciplinas reprovadas** abaixo, em largura total. Cada card
    de disciplinas mostra o **docente** e tem **campo de busca** (filtra por
    código, componente ou docente, com contador "N de N"). Disciplinas em que o
    aluno está matriculado agora recebem o badge "Matriculado".
  - **Status semestre atual** — dados resumidos do aluno e, abaixo, duas
    colunas: à esquerda o card "Disciplinas do semestre atual", à direita
    **"Performance no semestre (frequência)"**. A leitura dos diários é em
    **lote**: uma única caixa "Selecionar diários PDF" (upload múltiplo) no
    topo do card esquerdo — o usuário sobe de uma vez todos os diários que
    tiver, sem precisar indicar qual arquivo é de qual disciplina. Ao clicar
    em **"Processar diários enviados"**, a disciplina de cada arquivo é
    identificada automaticamente (`POST /check-disciplines`, uma chamada com
    todos os arquivos) e o resultado de cada um cai na linha certa da lista
    (localizado pelo **código** da disciplina, não pela ordem de upload);
    a frequência é calculada com uma única chamada a `POST /analyze-frequency`
    para o lote inteiro. Por disciplina mostra **aulas dadas**, **faltas** e
    **% de frequência** (vermelho abaixo de 75%), com detalhamento por mês em
    "Ver por mês". Diários que não correspondem a nenhuma disciplina do
    semestre atual (código não bate) geram um aviso listando quais, sem travar
    o processamento dos demais; processar um novo lote não apaga os resultados
    de disciplinas de lotes anteriores. O card da direita consolida as
    disciplinas já processadas num **gráfico de barras horizontais** (uma
    barra por disciplina, verde quando a frequência ≥ 75% e **vermelha quando
    abaixo de 75%**, com uma **linha vertical tracejada no marco de 75%** e
    tooltip com aulas/faltas/frequência), atualizado a cada lote processado;
    mostra um estado vazio até a primeira análise. Se a matrícula do aluno não aparecer
    no diário identificado, avisa que pode ser o PDF errado e a disciplina
    fica de fora do gráfico.
  - **JSON** — retorno bruto da API com botão "Copiar JSON".

  As abas **Visão geral** e **Status semestre atual** têm um botão **"Ajuda"**
  (canto superior direito do conteúdo) que abre um diálogo explicando os
  elementos daquela página especificamente.

  > Havia uma aba "Áreas temáticas" (classificação das disciplinas por eixo do
  > currículo) — **removida** por ser específica do curso BSI/São Borja; a
  > ferramenta deve valer para qualquer curso. Histórico no git (`57aff17`).
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
- **PDF da análise individual** (histórico): cabeçalho, dados do aluno,
  progresso do curso, desempenho por semestre (tabela + gráfico) e as tabelas de
  disciplinas aprovadas / a cursar / reprovadas (com docente) — usa
  `jspdf-autotable`.
- Cabeçalho institucional com logo do IFFar (`iffar-horizontal.png`),
  convertido para base64 na geração.

### Privacidade
- Processamento sob demanda; nenhum dado de aluno é armazenado — os PDFs são
  gravados em diretório temporário no servidor e descartados ao fim da
  requisição (`tempfile.TemporaryDirectory`).

---

## Como rodar localmente

Com Docker (substitui o XAMPP):

```bash
docker compose up -d --build
```

- Front-end: `http://localhost:8085` (nginx, sem cache no navegador em dev —
  `nginx-dev.conf`).
- API: `http://localhost:5001`. Precisa da chave da conta de serviço do
  Firebase salva como `firebase-service-account.json` na raiz do projeto
  (ignorada pelo git), montada no container. Para testar os e-mails, crie um
  `.env` (também ignorado) com `SMTP_USER` e `SMTP_PASSWORD` — é opcional.
- Para o front usar a API local, alterne a constante `API_URL` para
  `http://127.0.0.1:5001` em `auth.html`, `presente.html` e `admin.html` —
  e volte para a da Vercel antes de commitar.

---

## Dependências (`requirements.txt`)

`flask`, `flask-cors`, `pandas`, `pdfplumber`, `werkzeug`, `firebase-admin`, `tzdata`
