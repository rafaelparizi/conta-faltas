# presente.edu — Funcionalidades Implementadas

Ferramenta gratuita para docentes e coordenações de curso do IFFar transformarem
os diários de classe exportados do **SIGAA** (PDF) em indicadores visuais de
frequência da turma e alertas de possível evasão.

> Status: **versão beta em testes**.

---

## Visão geral da arquitetura

| Camada | Tecnologia | Arquivo |
|---|---|---|
| Landing page | HTML + Tailwind (CDN) | `index.html` |
| Autenticação | Firebase Authentication (Google) | `auth.html`, `auth-common.js`, `firebase-config.js` |
| Gestão de acessos | HTML + Tailwind | `admin.html` |
| Perfil do usuário | HTML + Tailwind | `perfil.html` |
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
  - **novo** → formulário "Solicitar acesso": nome, **modalidade do curso**
    (Técnico Integrado, Técnico Subsequente, Superior, EJA, Pós-graduação),
    curso que coordena, **portaria de designação** (PDF/imagem, até 700KB) e a
    **vigência da portaria** (início + término previsto; o término é sugerido
    como a véspera do mesmo dia 24 meses depois), enviado para
    `POST /auth/solicitar`;
  - **pendente** → "Solicitação em análise";
  - **rejeitado** → "Acesso não aprovado";
  - **revogado** → "Acesso revogado" com o motivo, e botão "Enviar nova portaria";
  - **expirado** → "Portaria vencida" (vigência terminou), e botão "Enviar nova portaria".
- **Admin** (`ADMIN_EMAILS` em `api/app.py`, hoje só
  `rafael.parizi@iffarroupilha.edu.br`): sempre aprovado, sem depender do
  Firestore. Vê o link **"Solicitações de acesso"** no rodapé do menu, que
  abre `admin.html` (**Gestão de acessos**):
  - **Solicitações pendentes**: dados do pedido (com modalidade), portaria
    (aberta via Blob URL — PDF por `data:` URL não abre no Chrome —, com
    "Abrir em nova aba" e "Baixar") e as datas de vigência **pré-preenchidas
    com as informadas, para o admin confirmar ou ajustar**. **Aprovar** exige
    as duas datas; elas viram a vigência do acesso (`/admin/pendentes`,
    `/admin/comprovante/<email>`, `/admin/decidir`).
  - **Coordenadores com acesso** (`/admin/coordenadores`): selo **Vigente**,
    **Vence em N dias** (≤ 30) ou **Vencida**; botão **Revogar** com motivo
    obrigatório e motivos prontos ("Término de vigência da portaria",
    "Substituição na coordenação do curso", "A pedido do(a) coordenador(a)")
    (`/admin/revogar`).
- **Vigência**: o acesso vale até o fim do dia de término confirmado
  (horário de São Paulo); depois é **bloqueado automaticamente** (status
  `expirado`). Revogado ou vencido pode reenviar portaria → volta a
  `pendente`.
- **Notificações por e-mail** para quem pediu acesso, enviadas por SMTP com a
  conta do admin (`SMTP_USER` / `SMTP_PASSWORD`; `Reply-To` sempre
  `rafael.parizi@iffarroupilha.edu.br`):
  1. ao enviar o pedido — "solicitação recebida, em análise" (com a vigência
     informada); **o admin também recebe um e-mail com a portaria anexada**
     (`Reply-To` = quem pediu);
  2. ao aprovar — acesso aprovado, com o passo a passo para entrar e a data
     até quando o acesso vale;
  3. ao recusar — não aprovado, com orientação para entrar em contato por e-mail;
  4. ao revogar — o motivo e como pedir de novo com uma nova portaria.

  Falha no envio (ou SMTP não configurado) nunca bloqueia o pedido nem a
  decisão: a API devolve `email_enviado: false` e o `admin.html` avisa para
  contatar a pessoa manualmente. Variáveis opcionais: `SMTP_HOST` (padrão
  `smtp.gmail.com`), `SMTP_PORT` (padrão `587`; `465` usa SSL direto) e
  `APP_URL` (link de acesso nos e-mails).
- **Status de acesso** (`_situacao_acesso`), nesta ordem: aprovado dentro da
  vigência > pedido pendente > revogado > vigência vencida > recusado >
  novo.
- **Fuso horário**: o Firestore guarda datas em UTC; a API devolve toda data
  no fuso de **São Paulo** (ISO 8601 com offset, ex.:
  `2026-09-26T11:40:25-03:00`) e o `admin.html` exibe com
  `timeZone: 'America/Sao_Paulo'`, independente do fuso do navegador.
- **Comprovante no Firestore**, em base64 dentro do documento da solicitação
  — sem Firebase Storage, que exige plano pago (Blaze) para ser habilitado.
- **A API também exige login**: todas as rotas de dados (`/check-disciplines`,
  `/analyze-frequency`, `/analyze-historico`) exigem o ID token do
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

## 0.1 Meu perfil, instituição e nível de ensino

- **Meu perfil** (`perfil.html`, link no rodapé do menu), em abas:
  - **Meus dados** (somente leitura): nome, e-mail, curso, modalidade e
    vigência do acesso — vêm do pedido aprovado;
  - **Instituição**: instituição, campus, sigla, **logo** (PNG/JPG/WEBP até
    300KB; SVG recusado) e endereço (CEP com preenchimento automático via
    ViaCEP, logradouro, número, complemento opcional, bairro, cidade, UF).
    Pode salvar incompleto. `GET/PUT /perfil`, guardado em `perfis/{email}`.
  - `perfil.html#instituicao` abre direto na aba da instituição.
- **Topo da ferramenta** (`presente.html`): logo, sigla e campus do perfil
  (substituem o logo fixo do IFFar) e, enquanto o perfil estiver
  incompleto, um card "Complete o perfil da sua instituição".
- **Relatórios em PDF usam os dados do perfil** (logo sem distorcer,
  instituição/campus no cabeçalho, instituição + endereço + paginação no
  rodapé, sigla no nome do arquivo) e **só são gerados com o perfil
  completo** — senão aparece um aviso com o botão "Completar perfil agora".
- **Nível de ensino** = modalidade aprovada do coordenador (fixo). Destaque
  colorido no topo da ferramenta com o nível. O **admin** escolhe o nível
  num seletor ("Ver como") — enviado à API no header `X-Nivel`, que só é
  aceito de admin.
- **Operações por nível** (`NIVEIS` em `api/app.py`): cada nível tem sua
  tabela de períodos por dia pela CH e seu leitor de histórico.
  - Leitura dos diários (**Frequência Turma**): igual em
    todos os níveis; muda só a sugestão de períodos/dia (técnico, subsequente
    e EJA: 40h→1, 80h→2, 120h→3, outras→1; superior e pós: 36h→2, 72h→4,
    outras→2). O botão Integrado/Superior por disciplina saiu do modal.
  - **Análise individual (histórico)**: só **Superior** tem leitor hoje; nos
    outros níveis o item some do menu e a API responde 501.

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

Sugestão inicial pela carga horária semestral, **conforme o nível de ensino
de quem usa** (`NIVEIS` → `periodos_sugeridos` / `inferir_peso_disciplina`):

| Nível | CH | períodos/dia | outra CH |
|---|---|---|---|
| Superior, Pós-graduação | `36h` / `72h` | 2 / 4 | 2 |
| Técnico Integrado, Técnico Subsequente, EJA | `40h` / `80h` / `120h` | 1 / 2 / 3 | 1 |

- O nível vem do coordenador (modalidade aprovada), não da disciplina — o
  `check-disciplines` devolve `nivel_sugerido` = nível do usuário.
- O front-end **sempre** exibe a tela de configuração e envia um mapa explícito
  `código → períodos` (`pesos`) que tem prioridade sobre a inferência. O usuário
  pode ajustar quando a disciplina tem aula em mais de um dia por semana.

### Endpoints

| Método/Rota | Função |
|---|---|
| `GET /` | Health-check da API |
| `POST /check-disciplines` | **Etapa 1** — pré-análise: retorna metadados de cada PDF (deduplicados por código) com `peso_sugerido` e `nivel_sugerido` pela CH; `requer_confirmacao` sinaliza CH que pode ser distribuída em mais de um dia (72/80/120h) |
| `POST /analyze-frequency` | **Etapa 2** — frequência da turma: por mês, aulas, faltas, % de presença, **datas das faltas** (`<Mês>_Datas_Faltas`, ex. `12 (2f), 19 (1f)`) e **possível evasão** (`<Mês>_Evasao`: as duas últimas marcações preenchidas do mês são faltas); mais os totais gerais |
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
  as análises, upload dos PDFs, **indicador de carregamento**,
  botão "Sair" e crédito) e **área de conteúdo à direita** com título dinâmico,
  ação de PDF, cartões de indicadores, resumo por disciplina e o conteúdo em abas.
- A **animação de carregamento** aparece na barra lateral, logo abaixo do botão
  "Processar relatório".
- O **resumo "Disciplinas × alunos"** fica acima da tabela (antes do conteúdo em
  abas), em **cards compactos** dispostos em grade de até 4 por linha, com filtro
  rápido por disciplina.
- **Duas abas de conteúdo** (a de gráfico é a **ativa por padrão**):
  1. **Gráfico da turma** — o gráfico geral (uma linha por aluno) embutido na
     página, com seus filtros e legenda.
  2. **Planilha de alunos** — a tabela detalhada por aluno/mês com busca e
     paginação.
- Responsivo: em telas estreitas a barra lateral vira um bloco no topo.

### Modos (navegação da barra lateral)
- **Frequência Turma** — análise dos diários de classe, com filtro de meses
  (comportamento descrito abaixo). Substituiu "Frequência geral" e "Busca por mês".
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
   por disciplina, escolhem-se os **períodos por dia de aula** (1 a 4),
   pré-preenchidos pela sugestão da carga horária no **nível de ensino** do
   usuário (fixo, ver seção 0.1).
3. Processa e exibe os resultados; o filtro de meses define o período e as
   abas de conteúdo alternam entre gráfico e planilha.

### "Frequência Turma"
- **Filtro de meses**: botão "Todos" + um botão por mês **com aula dada** nos
  diários (meses futuros ainda vazios não aparecem), marcáveis juntos. Tudo na
  tela — planilha, gráfico, indicadores e PDF — usa só os meses do período, com
  aulas, faltas e % **recalculados no período** (`resumoPeriodo`); com "Todos"
  o resultado é o total geral.
- Tabela com presença por mês (ordem do calendário) e, no fim, faltas e % do
  período ("Geral" com Todos) e a coluna **Datas das faltas** do período.
- **Evadido** (linha inteira em **vermelho-claro**, selo "evadido"): faltas
  acumuladas **no semestre** (todos os meses, independente do filtro)
  atingiram **25% da carga horária** da disciplina — o limite de reprovação por
  falta (ex.: 36h → 9 faltas; 72h → 18).
- **Possível evasão** (linha inteira em **amarelo**, selo "possível evasão"):
  faltou nas duas últimas aulas registradas do **último mês do período que
  teve aula** (com um mês só, é a regra da antiga "Busca por mês"). Quem já é
  evadido aparece só como evadido.
- Legenda das cores na planilha e três filtros de situação independentes,
  **"Abaixo de 75%"**, **"Evadidos"** e **"Possível evasão"** — como botões na
  planilha **e clicando nos cards** de mesmo nome (sincronizados; o card ativo
  ganha contorno e leva à aba da planilha). Nenhum ligado mostra todos; com
  vários, vale a união dos grupos. Os números dos cards são contados antes
  desses filtros (só busca e período), então não mudam ao clicar.
- Células coloridas conforme o **limiar crítico de 75%** (verde acima /
  vermelho abaixo).
- Ordenação por faltas ou por % do período.
- Busca textual e filtro por disciplina.
- Paginação (35 itens por página) com navegação e atalhos para primeira/última
  página.

### Painel de indicadores (KPIs)
- Total de alunos.
- Frequência média no período.
- Abaixo de 75% no período.
- Evadidos (≥ 25% da CH em faltas no semestre).
- Possível evasão (sem contar os evadidos).
- Card com resumo por disciplina (quantidade, badge de nível e filtro rápido),
  acima da tabela.

### Gráficos (Chart.js)
- **Gráfico individual do aluno**: série temporal de % de presença por mês, com
  faixas de fundo coloridas por faixa de risco e linha do limiar de 75%
  (plugin `monthBandPlugin` customizado).
- **Gráfico da turma** (aba de conteúdo, não é mais modal): uma linha por aluno,
  eixo X = **os meses do período filtrado**; cor, faixa (slider) e filtros
  acima/abaixo usam o % do período.
  - Filtro por disciplina.
  - Filtro por faixa de percentual com **slider duplo** (mín/máx).
  - Filtros rápidos "acima de 75%" / "abaixo de 75%".
  - Legenda interativa: ligar/desligar alunos individualmente, mostrar todos,
    ocultar todos.
  - Contador de alunos exibidos.

### Relatórios em PDF (jsPDF)
- **PDF da frequência da turma** (respeita o período, a busca e os filtros de situação): por aluno e disciplina, cada mês do período com aulas, faltas, % e
  as **datas das faltas**; resultado do período e marcação de evadido ou de
  possível evasão.
  Nome do arquivo com o período. Substituiu o PDF de detalhamento mensal.
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
