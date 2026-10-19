# C4 — Nível 4: Diagrama de código (reflete a implementação real)

Nomes exatos de classes/funções de `app/` conforme implementados (este diagrama foi
escrito após o código e validado contra os arquivos).

```mermaid
classDiagram
  direction LR

  class TaskManager {
    +criar(tipo: str, params: dict, icp_id: int?, aprovacao_id: int?) int
    +iniciar(tarefa_id: int, executar: Callable, timeout_s: int?) None
    +cancelar(tarefa_id: int) bool
    -_ativas: dict[int, TarefaHandle]
  }
  class TaskContext {
    +tarefa_id: int
    +prazo_monotônico: float
    +check() None «levanta TarefaCancelada/TarefaExpirada»
    +evento(mensagem, nivel, etapa, dados) None
    +progresso(valor: int) None
    +cancelar() None
  }
  class TarefaHandle {
    +contexto: TaskContext
    +thread: threading.Thread
  }
  class TarefaCancelada «exception»
  class TarefaExpirada «exception»

  class Research {
    «módulo app.engine.research»
    +executar_pesquisa(ctx, params) dict
    +dominio_de(url) str?
    +dominio_corresponde_ao_nome(dominio, nome) bool
    -_com_tentativas(ctx, descricao, fn, tentativas)
    -_procurar_site_via_busca(ctx, candidato, bloqueios) str?
    -_encontrar_duplicata(dominio, nome, localizacao) int?
  }
  class Scoring {
    «módulo app.engine.scoring»
    +pontuar(criterios, evidencias, sinais_inferencia, url_fonte_inicial) dict
  }
  class Evidence {
    «módulo app.engine.evidence»
    +extrair_pagina(url, html) PaginaColetada
    +coletar_evidencias(pagina, criterios, url, data, max) list[dict]
    +html_para_texto(html) str
    +hash_conteudo(texto) str
  }
  class PaginaColetada {
    +url, titulo, texto, meta_description, h1
    +cnpjs, telefones, ceps, emails
  }
  class ICP {
    «módulo app.engine.icp»
    +extrair_criterios(texto) dict
    +consultas_pesquisa(criterios) list[str]
  }
  class Drafting {
    «módulo app.engine.drafting»
    +redigir(nome, local, evidencias, proposta, assinatura) str
    +redigir_assistido(...) tuple[str, bool]
  }
  class Approvals {
    «módulo app.engine.approvals»
    +solicitar(tipo_acao, resumo, conteudo, params, tarefa_id?) int
    +decidir(aprovacao_id, aprovar: bool) dict
    -_executar_aprovada(ctx, params) dict
    -_hash(conteudo) str
  }
  class LLM {
    «módulo app.engine.llm»
    +disponivel() bool
    +completar_json(system, user) dict|list|None
  }

  class Sites {
    «app.adapters.sites»
    +coletar_site(url) ResultadoColeta
    +verificar_robots(url) tuple[bool, str]
  }
  class ResultadoColeta {
    +url, url_final, estado, http_status, html, detalhe
  }
  class BloqueioDetectado «exception»
  class Nominatim {
    «app.adapters.nominatim»
    +buscar(consulta, limite, somente_brasil) list[dict]
  }
  class FonteBloqueada «exception»
  class Websearch {
    «app.adapters.websearch»
    +buscar(consulta, limite) list[str]
  }
  class BuscaBloqueada «exception»
  class BrasilAPI {
    «app.adapters.brasilapi»
    +consultar_cnpj(cnpj_formatado) dict|None
  }
  class MacosApps {
    «app.adapters.macos_apps»
    +notes_criar_nota(titulo, corpo) dict
    +mail_criar_rascunho(para, assunto, corpo) dict
    +executar_osascript(script, timeout) str
  }
  class PermissaoNegada «exception»
  class BrowserBridge {
    «app.adapters.browser»
    +status() dict
    +atender(ws: WebSocket) None «loop de mensagens»
    +criar_pedido(tipo, params) int
    +aguardar_resposta(pedido_id, timeout_s) dict|None
    -_enviar(payload) None
    -_receber_resposta(resposta) None
  }
  class DB {
    «app.db»
    +inserir(tabela, valores) int
    +atualizar(tabela, id, valores) None
    +buscar_um(tabela, id) Row?
    +listar(tabela, where, params, ordem, limite) list[Row]
    +registrar_auditoria(ator, acao, detalhes) None
    +init_schema() None
  }

  TaskManager o-- TarefaHandle
  TarefaHandle o-- TaskContext
  TaskContext ..> TarefaCancelada : levanta
  TaskContext ..> TarefaExpirada : levanta
  TaskManager ..> DB : estado/eventos
  TaskManager ..> "research.executar_pesquisa" : executar(ctx, params)

  Research ..> TaskContext : check()/evento()/progresso()
  Research ..> Nominatim : buscar()
  Research ..> Websearch : buscar()
  Research ..> Sites : coletar_site()
  Research ..> BrasilAPI : consultar_cnpj()
  Research ..> Evidence : extrair_pagina/coletar_evidencias
  Research ..> Scoring : pontuar()
  Research ..> DB : gravar empresa/eventos
  Nominatim ..> FonteBloqueada : levanta
  Websearch ..> BuscaBloqueada : levanta
  Sites ..> BloqueioDetectado : levanta
  Sites ..> ResultadoColeta : devolve
  Evidence ..> PaginaColetada : produz

  Approvals ..> DB : aprovação/auditoria
  Approvals ..> TaskManager : criar/iniciar executora
  Approvals ..> MacosApps : ação real (Notes/Mail)
  MacosApps ..> PermissaoNegada : TCC −1743
  Drafting ..> LLM : opcional

  BrowserBridge ..> DB : pedidos/respostas
```

## Observações de fidelidade

- `TaskManager`, `TaskContext`, `BrowserBridge` são as únicas classes com estado; os
  demais “componentes” do nível 3 são módulos com funções puras ou quase puras —
  representados aqui como classes estereotipadas `«módulo»`.
- Exceções de controle (`TarefaCancelada`, `TarefaExpirada`, `BloqueioDetectado`,
  `BuscaBloqueada`, `FonteBloqueada`, `PermissaoNegada`) são a espinha dorsal do
  comportamento honesto: interrompem o fluxo e são traduzidas em eventos/estados.
- Fonte da verdade: `app/engine/tasks.py`, `app/engine/research.py`,
  `app/engine/approvals.py`, `app/adapters/*.py`, `app/db.py`.
