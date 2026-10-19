# C4 — Nível 1: Diagrama de contexto

```mermaid
C4Context
  title Contexto — AI Prospecting Employee (execução local no Mac do usuário)

  Person(usuario, "Usuário (vendedor)", "Descreve o ICP, revisa critérios, aprova ações")

  System(aipe, "AI Prospecting Employee", "Monólito modular local: interface pt-BR, motor do agente, persistência SQLite")

  System_Ext(fontes, "Fontes públicas autorizadas", "OpenStreetMap/Nominatim, sites das empresas, DuckDuckGo HTML, BrasilAPI (dados abertos Receita Federal)")
  System_Ext(navegador, "Navegador do usuário (perfil autenticado)", "Chrome/Edge/Brave com a extensão MV3 carregada")
  System_Ext(apps_macos, "Aplicativos do macOS", "Apple Notes e Apple Mail (via AppleScript/TCC)")

  Rel(usuario, aipe, "Usa via navegador em http://127.0.0.1:8734", "HTTP local")
  Rel(aipe, fontes, "Consulta e coleta evidências (robots.txt e limites de taxa respeitados)", "HTTPS")
  Rel(usuario, navegador, "Instala a extensão e autoriza pedidos (Permitir/Negar)")
  Rel(navegador, aipe, "WebSocket local ws://127.0.0.1:8734/ws/extensao — entrega conteúdo da aba autorizada")
  Rel(aipe, apps_macos, "Cria nota/rascunho após aprovação explícita (hash SHA-256 do conteúdo)", "AppleScript (TCC)")

  UpdateRelStyle(usuario, aipe, $offsetX="-40", $offsetY="-30")
  UpdateRelStyle(navegador, aipe, $offsetX="10", $offsetY="20")
```

## Notas do nível de contexto

- O usuário é a **única fonte de autorização** para ações externas e para o uso do
  navegador autenticado.
- Nenhum sistema externo recebe dados privados: as requisições às fontes carregam apenas
  consultas de pesquisa e User-Agent próprios; o WebSocket da extensão escuta somente em
  `127.0.0.1`.
