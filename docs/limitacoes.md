# Limitações conhecidas

Registro honesto do que funciona, do que não pôde ser validado neste ambiente e como
validar no seu Mac.

## 1. Motores de busca bloqueiam a rede atual (NordVPN)

**Fato observado (confirmado 2×).** DuckDuckGo HTML responde `HTTP 202` com página de
“anomaly detection” tanto do ambiente de desenvolvimento quanto da rede do usuário
**com NordVPN ativa** (saída por datacenter é o alvo típico desse anti-bot). Bing também
devolve challenge. A página de anomalia virou fixture e o teste
`test_busca_web_detecta_bloqueio_antibot` garante que o agente **interrompe e registra** a
limitação em vez de insistir.
**Como resolver.** Desligar a VPN (ou usar outra rede) e rodar uma pesquisa: os eventos da
tarefa mostrarão os sites encontrados via busca. Nada para ativar no código — a fonte já
está implementada com detecção honesta de bloqueio.

## 2. Cobertura do OpenStreetMap é desigual

**Fato observado.** “fisioterapia Campinas” e “academia São Paulo” retornam bons POIs;
“advogado Rio de Janeiro” retorna apenas ruas (filtradas) e zero estabelecimentos.
**Impacto.** Alguns ICPs terão poucos ou nenhum candidato de fontes públicas.
**Mitigação já implementada.** Filtro por classe de estabelecimento (`category` do
jsonv2), aviso transparente no histórico, canal do navegador do usuário como alternativa
para fontes que exigem login.

## 3. Extensão do navegador: status da ativação (Chrome 154 Stable)

**Contexto.** O Chrome Stable a partir da 136 **ignora** o flag `--load-extension`, então
não há carregamento por linha de comando; a via correta é a página `chrome://extensions`.
**Tentativa de automação.** A automação dos cliques via Acessibilidade do macOS foi
bloqueada por TCC (“osascript is not allowed assistive access” −1719). Dois caminhos:
1. **Manual (2 min):** com `chrome://extensions` já aberto e a pasta `extension/` aberta no
   Finder — ativar **Modo do desenvolvedor** → **Carregar sem compactação** → selecionar a
   pasta `extension` → clicar no ícone da extensão → **Conectar**. A conexão é verificável
   no app (selo “navegador conectado”).
2. **Automatizada:** conceder Acessibilidade ao terminal que executa o ZCode
   (Ajustes do Sistema → Privacidade e Segurança → Acessibilidade) e avisar o agente, que
   conclui os cliques e a verificação sozinho.
**Feito e validado.** Extensão completa (popup Permitir/Negar, reconexão, `activeTab` +
`executeScript` lendo só texto visível) e protocolo coberto por teste automatizado
(`tests/test_extensao_protocolo.py`). Ao carregar, a extensão se conecta sozinha ao
servidor local (`onInstalled` → WebSocket) — a conexão aparece na auditoria.

## 4. AppleScript pede permissão TCC na primeira execução

Ao aprovar a primeira ação no Notes/Mail, o macOS exibe o diálogo “<terminal> deseja
controlar Notes/Mail”. Se negado, o app mostra instrução de liberação (Privacidade e
Segurança → Automação). Neste ambiente de desenvolvimento, a ação foi executada com
sucesso e a nota foi conferida no app.

## 5. Heurísticas com casos de borda

- `dominio_corresponde_ao_nome` pode falhar para marcas cujo domínio não deriva do nome
  (ex.: “Clínica X” com domínio da holding). Nesses casos a página é tratada como
  “página relacionada” — seguro, porém menos informativo.
- POIs do OSM com nome genérico (“Academia”) aparecem vários; deduplicação só mescla
  nome+endereço idênticos ou mesmo domínio.
- Busca de telefone/CEP usa regex simples; servem como evidência, não como dado
  estruturado garantido.

## 6. Ambiente de automação da interface

Durante a verificação visual com navegador automatizado, cliques via locator ficaram
instáveis por mutações periódicas do indicador de status (polling de 4 s) — limitação do
hóspede de automação, não do app: os mesmos fluxos formam validados por HTTP nos testes
e2e e por execuções reais com `curl`. Ainda assim, se quiser eliminar a causa, o backlog
P2 propõe atualizar o selo de status só quando o texto mudar de fato.
