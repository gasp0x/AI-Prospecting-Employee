# Decisões técnicas (ADRs resumidas)

## ADR-001 — Python 3.11 + FastAPI/Uvicorn como base do monólito

**Contexto.** MacBook Pro Intel (macOS 15.7, i7-9750H, 16 GB). Era preciso um processo
local servindo interface, API JSON e WebSocket, com boa ergonomia para lógica de agente
e parsing de HTML, e dependências com wheels para macOS x86_64.
**Decisão.** Python 3.11 (já instalado: 3.11.6) + FastAPI 0.149 + Uvicorn 0.54
(licenças MIT/BSD; HTTP e WebSocket no mesmo processo). Motor síncrono em threads.
**Consequências.** Sem etapa de build; installs rápidos; threads bastam para 1 usuário
local. Se houver escala multiusuário, migrar partes do motor para asyncio é local, pois os
adaptadores já isolam I/O.

## ADR-002 — SQLite (stdlib) para persistência

**Contexto.** Persistência local, deduplicação, auditoria e histórico sem serviço externo.
**Decisão.** `sqlite3` da biblioteca padrão em modo WAL, uma conexão por operação.
**Consequências.** Zero dependências nativas (nada para compilar no Intel), backup =
copiar arquivo, consultas SQL diretas para exportação/auditoria. Limites de escrita
concorrente são irrelevantes para uso local mono-usuário.

## ADR-003 — Interpretação do ICP determinística, com LLM opcional

**Contexto.** O agente não pode inventar dados; a extração de critérios precisa ser
testável e funcionar offline.
**Decisão.** Parser determinístico pt-BR (dicionário de segmentos, estados/cidades,
padrões “que tenham/usem…”, “mais de N funcionários”). LLM (endpoint compatível com
OpenAI, `LLM_*` no `.env`) é opcional e apenas auxilia; sem chave, tudo funciona.
**Consequências.** Testes estáveis sem rede; comportamento reproduzível; LLM nunca é
fonte de empresas/evidências.

## ADR-004 — Fontes de descoberta: Nominatim primeiro, busca web com bloqueio honesto

**Contexto.** Motores de busca bloqueiam IPs de datacenter (verificado: DDG respondeu
HTTP 202 com página “anomaly” neste ambiente — fixture usada nos testes).
**Decisão.** Nominatim/OpenStreetMap como fonte primária de descoberta (POIs reais,
política documentada: 1 req/s + User-Agent), filtrando apenas classes de estabelecimento
(`category` em jsonv2) para não coletar ruas com nome “Advogado…”. DuckDuckGo HTML como
fonte secundária com detecção de bloqueio; ao detectar, o agente registra a limitação e
segue com as demais fontes. Sem tentativa de evasão.
**Consequências.** Cobertura desigual por segmento é esperada e é reportada com
transparência; na rede residencial do usuário a busca web tende a ampliar a cobertura.

## ADR-005 — Site oficial × página relacionada; CNPJ só na página oficial

**Contexto.** Na primeira execução real, links de anúncio (duckduckgo.com/y.js →
mercadolivre) e marketplaces (TotalPass) apareceram como “site” da empresa, e um CNPJ de
terceiro foi atribuído à empresa errada.
**Decisão.** Links de motores/anúncios e domínios irrelevantes vão à lista negra. Uma URL
achada por busca só é tratada como **site oficial** se o domínio contém um token
distintivo do nome da empresa (heurística `dominio_corresponde_ao_nome`); caso contrário é
**página relacionada** — ainda fonte válida de evidência, mas não aparece como site.
Enriquecimento por CNPJ só ocorre em página oficial.
**Consequências.** Menos falsos “sites oficiais”; evidências de terceiros seguem úteis
para corroborar critérios.

## ADR-006 — Pontuação explicável com quatro status

**Contexto.** Uma nota única “100” para empresas comprovadas apenas pelo registro de
descoberta (OSM) superestimava a aderência.
**Decisão.** Por critério: **atendido** (fato corroborado por fonte independente — peso
cheio), **parcial** (apenas a fonte de descoberta — 0,7×), **inferido** (sinal indireto
etiquetado, ex.: domínio .br — 0,5×), **ausente** (0; ausência ≠ negativa).
**Consequências.** Ranking mais honesto; a UI mostra a natureza de cada evidência.

## ADR-007 — Extensão MV3 + WebSocket local (em vez de Native Messaging)

**Contexto.** Requisito: operar aba autenticada do navegador do usuário via extensão
autorizada, sem exportar cookies/senhas.
**Decisão.** Extensão Manifest V3 com service worker conectando em
`ws://127.0.0.1:8734/ws/extensao` (token opcional). Pedidos do agente (`ler_aba`,
`abrir_url`) aparecem no popup e só executam após clique em “Permitir”. Leitura usa
`activeTab` + `chrome.scripting.executeScript` extraindo URL/título/texto visível.
**Consequências.** Instalação por “Carregar sem compactação” (sem loja); o WebSocket
mantém o service worker vivo (Chrome 116+); permissão granular por pedido; nenhum dado
sai da máquina.

## ADR-008 — Ações no macOS via AppleScript (`osascript`)

**Contexto.** Demonstrar ação real em app local com permissão do usuário.
**Decisão.** `subprocess` + `osascript` para Notes (criar nota) e Mail (criar **rascunho**,
nunca enviar). Erro −1743 (TCC negado) vira mensagem orientando liberar em Privacidade e
Segurança → Automação.
**Consequências.** Sem dependências; primeiro uso pede permissão via diálogo nativo;
strings escapadas para AppleScript; tudo auditado.

## ADR-009 — Aprovações com conteúdo exato + SHA-256

**Contexto.** Requisito: vincular cada aprovação à ação e ao conteúdo exatos.
**Decisão.** Tabela `aprovacao` guarda o conteúdo integral e o hash SHA-256 no momento da
solicitação; o executor re-verifica o hash antes de agir; divergência ⇒ tarefa com falha
e nova aprovação necessária. Decisões (aprovar/negar) e execuções vão à auditoria.
**Consequências.** Impossível executar conteúdo diferente do aprovado, mesmo com edição
concorrente no banco (coberto por teste).

## ADR-010 — Motor de tarefas em threads com cancelamento cooperativo

**Contexto.** Pesquisas demoram minutos; é preciso cancelar, prazos e histórico.
**Decisão.** `TaskManager` roda cada tarefa em uma `threading.Thread` com um
`TaskContext` (evento de cancelamento + prazo por `time.monotonic()`). Os executores
chamam `ctx.check()` entre passos; timeout de tarefa e tentativas por item com backoff
ficam no orquestrador. Eventos de histórico vão para `tarefa_evento`.
**Consequências.** Simples e robusto para uso local; cancelamento responde no próximo
ponto seguro (item de coleta), preservando o que já foi coletado.

## ADR-011 — Envio real de e-mail como ação aprovada (não como fluxo automático)

**Contexto.** O MVP entregava apenas rascunho no Mail. O usuário autorizou o “envio real
supervisionado”, mas enviar mensagem é a ação de maior consequência do produto — não pode
entrar por atalho.
**Decisão.** Nova ação `enviar_email_mail` no executor de aprovações: o AppleScript cria e
**envia** a mensagem (`send msg`) somente depois de uma aprovação `pendente → aprovada`
com o conteúdo integral + SHA-256, com destinatário validado e resumo explícito (“ao
aprovar, a mensagem sai da sua caixa — não há como desfazer”). A UI distingue visualmente
o botão de rascunho (neutro) do de envio real (vermelho, com confirm extra). Cada envio é
auditado (`macos_mail_enviar_email`).
**Consequências.** Nenhum envio em massa/automático — um e-mail por aprovação, com o
texto exato visível na tela de decisão; testes de executor (com `osascript` simulado)
garantem destinatário obrigatório e fidelidade do conteúdo.
