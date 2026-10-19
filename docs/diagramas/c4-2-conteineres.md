# C4 — Nível 2: Diagrama de contêineres

```mermaid
C4Container
  title Contêineres — AI Prospecting Employee (um processo Python + extensão)

  Person(usuario, "Usuário (vendedor)")

  System_Boundary(mac, "Mac do usuário (execução local)") {
    Container(ui, "Interface web pt-BR", "FastAPI + Jinja2", "Páginas: painel, ICP, tarefa, resultados, empresa, aprovações, navegador, exportar, auditoria")
    Container(api, "API JSON + WebSocket", "FastAPI", "/api/tarefa/{id}/estado, /api/extensao/status, /ws/extensao")
    Container(motor, "Motor do agente", "Python (threads)", "ICP→critérios, planejamento, descoberta, coleta, pontuação, redação, aprovações")
    ContainerDb(banco, "Banco local", "SQLite (WAL)", "icp, tarefa, tarefa_evento, empresa, rascunho, aprovacao, pedido_extensao, auditoria")
    Container(extensao, "Extensão do navegador", "Manifest V3 (JS)", "Service worker + popup: conexão WS, fila de pedidos, Permitir/Negar, leitura de aba ativa")
    Container(ponte, "Ponte do navegador", "FastAPI WebSocket", "Estado da conexão, entrega/backlog de pedidos, respostas")
  }

  System_Ext(fontes, "Fontes públicas (OSM, sites, DDG, BrasilAPI)")
  System_Ext(navegador_host, "Navegador do usuário", "perfil autenticado")
  System_Ext(apps_macos, "Apple Notes / Apple Mail")

  Rel(usuario, ui, "Opera o fluxo (ICP, revisão, aprovações)")
  Rel(ui, motor, "Inicia tarefas e aprovações")
  Rel(motor, banco, "Persiste tudo (eventos, evidências, auditoria)")
  Rel(api, banco, "Consulta estado/progresso para a UI")
  Rel(motor, fontes, "HTTPS (httpx) — coleta com robots.txt e limites")
  Rel(extensao, ponte, "WebSocket 127.0.0.1 (token opcional)")
  Rel(ponte, banco, "Registra pedidos e respostas")
  Rel(motor, ponte, "Cria pedidos ler_aba/abrir_url")
  Rel(extensao, navegador_host, "Lê aba ativa autorizada (activeTab + executeScript)")
  Rel(motor, apps_macos, "osascript — nota no Notes / rascunho no Mail (após aprovação)")

  UpdateRelStyle(extensao, ponte, $offsetX="0", $offsetY="-10")
```

## Notas do nível de contêineres

- **Um único processo** (`app/main.py` — FastAPI/Uvicorn) hospeda `ui`, `api` e `ponte`;
  o `motor` roda em threads do mesmo processo (monólito modular).
- A **extensão** é o único componente dentro do navegador; comunica-se exclusivamente com
  `127.0.0.1` e não tem permissões de leitura de cookies/histórico no manifest.
- O **banco** é o contrato entre motor e interface (estados, eventos e resultados
  sobrevivem a reinícios do processo).
