# Especificação — AI Prospecting Employee (MVP)

## Objetivo

Operar, com execução local no Mac do usuário, um “funcionário de IA” de prospecção
comercial que: interpreta um perfil de cliente ideal (ICP) em lingu natural, pesquisa
empresas em fontes públicas autorizadas, coleta evidências verificáveis, qualifica
oportunidades com pontuação explicável e prepara mensagens de abordagem para revisão
humana — sem inventar dados e sem executar ações externas sem aprovação explícita.

## Capacidades

0. **Interface conversacional.** Chat estilo ChatGPT (web) e canal **Telegram** opcional:
   o usuário dá ordens em lingu natural ("procure X em Y", "status", "rascunho para Z",
   "aprovar 3") e o funcionário executa e reporta no próprio chat, com o mesmo controle
   de evidências e aprovações da interface web.
1. **ICP → critérios revisáveis.** Texto livre em pt-BR vira critérios tipados
   (segmento, localização, porte, palavras-chave) com pesos editáveis na interface.
2. **Descoberta em fontes autorizadas.** OpenStreetMap/Nominatim (POIs reais, política
   de uso respeitada) e busca web DuckDuckGo HTML, com detecção honesta de bloqueio.
3. **Coleta com evidências.** Página inicial do site oficial (respeitando `robots.txt`),
   registro da fonte de descoberta e enriquecimento por CNPJ (dados abertos da Receita
   via BrasilAPI). Cada evidência guarda trecho original, URL, data e critério.
4. **Qualificação explicável.** Pontuação 0–100 com status por critério:
   fato corroborado (peso cheio), parcial (só a fonte de descoberta, 0,7×), inferência
   (0,5×, etiquetada) e ausente (0 — ausência não é negativa).
5. **Gestão de tarefas.** Estados (pendente/em execução/aguardando aprovação/concluída/
   falha/cancelada/expirada), progresso, tempo limite, tentativas limitadas com backoff,
   cancelamento cooperativo e histórico completo de eventos.
6. **Aprovações vinculadas ao conteúdo exato.** Toda ação externa relevante (criar
   rascunho de e-mail, criar nota, e no futuro enviar/publicar/convidar/excluir) exige
   aprovação com o conteúdo integral e SHA-256; a execução re-verifica o hash.
7. **Navegador do usuário.** Extensão MV3 conectada por WebSocket local opera abas do
   navegador real (autenticado) mediante “Permitir” por pedido; lê apenas texto visível.
8. **Ações locais no macOS.** Nota no Apple Notes, rascunho no Apple Mail e **envio real
   de e-mail pelo Mail** via AppleScript — sempre com aprovação hash-vinculada, permissão
   TCC do usuário e auditoria.
9. **Persistência, deduplicação, exportação e auditoria.** SQLite local; duplicatas por
   domínio (ou nome+endereço); exportação CSV/JSON; registro de auditoria de tudo.

## Restrições

- Execução local (monólito modular), sem nuvem obrigatória; LLM é opcional.
- Compatibilidade com MacBook Pro Intel (wheels macOS x86_64; sem compilação nativa).
- Credenciais apenas em `.env` (fora do controle de versão).
- Extensão lê somente o texto visível da aba autorizada; nunca cookies/senhas/localStorage.
- Fontes públicas respeitam `robots.txt`, limites de taxa e políticas de uso documentadas.
- Bloqueios/CAPTCHA interrompem a coleta com explicação — nenhuma tentativa de evasão.
- Nenhuma empresa/evidência é gerada por modelo de linguagem; LLM só auxilia
  interpretação e redação sobre fatos já coletados.

## Não objetivos (neste MVP)

- Envio automático/em massa de mensagens: o envio real existe, mas **somente** por mensagem,
  após aprovação humana com hash do conteúdo exato (ação `enviar_email_mail`); nenhuma
  plataforma externa é integrada além do Apple Mail local.
- Integrações com LinkedIn/CRM/APIs pagas; scraping de redes sociais; compra de leads.
- Enriquecimento financeiro (receita, headcount) de provedores comerciais.
- Multiusuário, autenticação de contas ou deploy em servidor compartilhado.
- Extração contínua/agendada em segundo plano (modo serviço).

## Critérios de sucesso do MVP (todos demonstrados)

1. Ao menos **5 empresas qualificadas com fontes reais** — obtido: 10 no ICP 1.
2. **Repetição com ICP diferente** produz resultados distintos — obtido: academias em SP.
3. **Recuperação de resultados persistidos** após reinício do servidor — demonstrado.
4. **Tratamento de falha e cancelamento comprovados** — bloqueio real de CAPTCHA
   interrompido com aviso; tarefa cancelada a 65% preservando coleta parcial; testes
   automatizados de falha/prazo.
5. **Uso do navegador autenticado** — protocolo da extensão validado por teste; acoplamento
   ao Chrome real documentado como passo manual (limitação do ambiente de desenvolvimento).
6. **Ação real no macOS com permissão** — nota criada no Apple Notes e confirmada;
   rascunho no Mail pelo mesmo mecanismo de aprovação.
