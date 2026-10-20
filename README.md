# AI Prospecting Employee

Funcionário de IA para **prospecção comercial com execução 100% local** no seu Mac.
Você descreve seu perfil de cliente ideal (ICP) em português; o agente transforma o texto
em critérios revisáveis, pesquisa empresas em fontes públicas autorizadas, coleta
**evidências verificáveis** (trecho + URL + data), calcula uma **pontuação de aderência
explicável**, redige uma mensagem de abordagem para revisão — e **só executa ações
externas com sua aprovação explícita**, vinculada ao conteúdo exato (hash SHA-256).

> MVP funcional, monólito modular, interface em português do Brasil, para MacBook Pro Intel.

---

## O que o MVP faz (validado neste ambiente)

| Capacidade | Status |
|---|---|
| ICP em texto livre → critérios revisáveis (segmento, localização, porte, palavras-chave) | ✅ funcionando |
| Descoberta de empresas reais via OpenStreetMap/Nominatim | ✅ funcionando (10 empresas/ICP nos testes) |
| Coleta do site oficial com `robots.txt`, tentativas limitadas e detecção de CAPTCHA/bloqueio | ✅ funcionando (bloqueio real detectado e interrompido) |
| Evidências com trecho original, URL de origem e data; enriquecimento por CNPJ (Receita via BrasilAPI) | ✅ funcionando |
| Pontuação explicável: fato corroborado / parcial (só fonte de descoberta) / inferência / ausente | ✅ funcionando |
| Duplicatas por domínio (e nome+endereço para POIs genéricos) | ✅ funcionando |
| Rascunho de abordagem gerado apenas com fatos coletados | ✅ funcionando |
| Aprovação humana vinculada ao conteúdo exato antes de ação externa/local | ✅ funcionando |
| Ação real em app do macOS (nota no Apple Notes; rascunho no Apple Mail) | ✅ funcionando |
| Tarefa: estados, progresso, tempo limite, tentativas, cancelamento e histórico | ✅ funcionando (cancelamento ao vivo testado) |
| Exportação CSV/JSON e registro de auditoria | ✅ funcionando |
| **Chat** (estilo ChatGPT) como interface principal, com histórico | ✅ funcionando |
| **Canal Telegram** (converse pelo celular; modelo OpenClaw) | ✅ funcionando — opcional, ativa com `AIPE_TELEGRAM_TOKEN` |
| Busca web multi-motor (DuckDuckGo → Brave) com bloqueio honesto | ✅ implementado; funciona quando o IP não está bloqueado por anti-bot |
| Envio real de e-mail pelo Mail, sempre com aprovação hash-vinculada | ✅ funcionando (opcional; o padrão continua sendo só rascunho) |
| Resultados persistidos e recuperáveis após reinício | ✅ funcionando |
| Navegador do usuário via extensão autorizada (aba autenticada) | ⚠️ implementado e testado; carregamento no Chrome (Stable 154) é manual — `chrome://extensions` → Modo do desenvolvedor → Carregar sem compactação → pasta `extension/` |
| Motores de busca web (DuckDuckGo HTML) | ⚠️ implementado; bloqueado com VPN de datacenter (verificado com NordVPN ativa — HTTP 202 anti-bot). Sem VPN/rede residencial tende a funcionar |

---

## Requisitos

- macOS 13+ em **Intel ou Apple Silicon** (validado no macOS 15.7, Intel Core i7, 16 GB)
- Python 3.11+ (`python3 --version`)
- Google Chrome, Edge ou Brave (para a extensão do navegador)
- Sem necessidade de chaves de API: o MVP funciona 100% determinístico e offline de serviços de IA

## Instalação

```bash
cd AI-Prospecting-Employee
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # opcional: ajuste porta, assinatura, token da extensão
```

## Execução

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8734
```

Abra **http://127.0.0.1:8734** — tudo roda na sua máquina; nenhum dado sai dela,
exceto as requisições HTTP públicas aos sites pesquisados (com seu User-Agent).

### Fluxo de uso — converse com o funcionário (http://127.0.0.1:8734)

```
Você: procure clínicas de fisioterapia em Campinas (SP), com site próprio, que atendam convênios
Você: status
Você: resultados
Você: rascunho para Clínica Dragão Divino
Você: aprovar 7        ← cria o rascunho no Mail (conteúdo exato, hash-verificado)
Você: exportar
```

O agente inicia a pesquisa sozinho e te avisa no chat quando terminar, com as melhores
empresas e pontuações. Os comandos de gerenciamento continuam disponíveis: **Painel**
(revisão de critérios com pesos), **Resultados** (ficha, fontes, evidências por empresa),
**Aprovações** (conteúdo exato + SHA-256), **Navegador**, **Exportar** e **Auditoria**.

### Telegram (opcional)

1. Crie um bot com o **@BotFather** no Telegram e copie o token.
2. No `.env`: `AIPE_TELEGRAM_TOKEN=123456:ABC-DEF…`
3. Reinicie o servidor e fale com o bot pelo celular — mesma conversa, mesmo cérebro,
   mesmas aprovações hash-vinculadas.

### Primeira ação no macOS (permissão TCC)

Ao aprovar a primeira nota/rascunho, o macOS pergunta se o terminal pode controlar
**Notes**/**Mail** (Automação). Aceite uma vez por app. Se negou sem querer:
Ajustes do Sistema → Privacidade e Segurança → Automação → habilite para o seu terminal.

## Extensão do navegador (aba autenticada)

Algumas páginas exigem login ou bloqueiam coleta automatizada. A extensão MV3 permite
operar **uma aba do SEU navegador já autenticado** — somente com clique explícito de
“Permitir” no popup, lendo **apenas o texto visível** da página. **Nunca** lê cookies,
senhas ou localStorage; nada sai da máquina (WebSocket em `127.0.0.1`).

Instalação:

1. Abra `chrome://extensions` → ative **Modo do desenvolvedor**.
2. **Carregar sem compactação** → selecione a pasta `extension/` deste projeto.
3. Clique no ícone da extensão → confirme o servidor `ws://127.0.0.1:8734/ws/extensao` → **Conectar**.
4. O selo no topo do app passa a “navegador conectado”. Pedidos do agente aparecem no popup com **Permitir/Negar**.

Para exigir token na conexão, defina `AIPE_TOKEN_EXTENSAO` no `.env` e informe-o no popup.

## Testes

```bash
.venv/bin/python -m pytest tests/ -q
```

40 testes: ICP → critérios; evidências; pontuação; tarefas (cancelamento, prazo, falha);
aprovações (hash, negação, adulteração); conectores (bloqueio anti-bot com fixture real,
robots.txt, CAPTCHA); protocolo WebSocket da extensão; e um fluxo ponta a ponta hermético
(ICP → pesquisa → qualificação → rascunho → aprovação → exportação). Detalhes em
[`docs/testes.md`](docs/testes.md).

## Estrutura

```
app/
  main.py            # FastAPI: UI + API + WebSocket num processo
  config.py          # .env e limites
  db.py              # SQLite (schema, helpers, auditoria)
  engine/            # motor do agente
    tasks.py         #   estados, prazo, cancelamento, histórico
    icp.py           #   texto → critérios (determinístico; LLM opcional)
    research.py      #   descoberta → coleta → qualificação
    evidence.py      #   trechos, metadados, hash
    scoring.py       #   pontuação explicável
    drafting.py      #   redação baseada em fatos
    approvals.py     #   aprovações com SHA-256 e executores
  adapters/          # Nominatim, sites (robots/CAPTCHA), websearch, BrasilAPI, macOS, ponte do navegador
  web/               # rotas pt-BR, API JSON, WS da extensão, templates
extension/           # Manifest V3: background + popup (Permitir/Negar)
tests/               # pytest (40 testes)
docs/                # especificação, decisões, limitações, testes, diagramas C4, backlog
```

## Documentação

- [Especificação (objetivo, capacidades, restrições, não objetivos, critérios de sucesso)](docs/especificacao.md)
- [Decisões técnicas (ADRs)](docs/decisoes.md)
- [Limitações conhecidas e o que fazer](docs/limitacoes.md)
- [Testes essenciais e evidências de validação](docs/testes.md)
- [Diagramas C4: contexto · contêineres · componentes · código](docs/diagramas/)
- [Backlog priorizado](docs/backlog.md)

## Limitações honestas (leia antes de usar)

- **Motores de busca bloqueiam IPs de datacenter** (e bloquearam este ambiente com
  HTTP 202/anomaly). O conector detecta, registra a limitação no histórico da tarefa e
  segue com as demais fontes. Da sua rede residencial o DDG HTML tende a responder.
- **Com NordVPN (ou outra VPN de datacenter) ativa, o DuckDuckGo bloqueia** a busca web (HTTP 202 anti-bot, verificado). Desligue a VPN para essa fonte entrar nas pesquisas.
- **Nominatim/OSM tem cobertura desigual** por segmento/cidade (advocacia no RJ, por
  exemplo, não tem POIs indexados — o app registra 0 candidatos com transparência).
- A extensão **não pôde ser acoplada a um Chrome real neste ambiente** (não há um
  navegador com o seu perfil aqui); o protocolo foi validado por teste automatizado e os
  passos manuais de validação estão em [`docs/limitacoes.md`](docs/limitacoes.md).
- O agente **não inventa** empresas ou evidências; critérios sem evidência ficam
  marcados como “ausente” — ausência não é tratada como negativa.

## Privacidade e segurança

- Credenciais ficam fora do código e do repositório (`.env` está no `.gitignore`).
- A extensão não acessa cookies/senhas/histórico; só texto visível da aba autorizada.
- Aprovações guardam o conteúdo integral + SHA-256; a execução re-verifica o hash —
  conteúdo alterado após a aprovação **não executa**.
- Toda ação relevante (ICP, pesquisa, aprovação, execução, conexão da extensão) vai para
  o registro de auditoria local.
