# Backlog priorizado

## P0 — feito no MVP (referência)

- [x] ICP em lingu natural → critérios revisáveis com pesos
- [x] Descoberta em fonte pública autorizada (Nominatim/OSM) com filtro de estabelecimentos
- [x] Coleta de site oficial com robots.txt, tentativas/backoff, detecção de CAPTCHA/bloqueio
- [x] Evidências (trecho, URL, data) + enriquecimento CNPJ (BrasilAPI) só em página oficial
- [x] Pontuação explicável (atendido/parcial/inferido/ausente) e deduplicação
- [x] Motor de tarefas: estados, prazo, tentativas, cancelamento, histórico
- [x] Aprovações com conteúdo exato + SHA-256 e executores (Notes, Mail-rascunho)
- [x] Extensão MV3 + WebSocket local com Permitir/Negar por pedido
- [x] Exportação CSV/JSON, auditoria, testes (40) e validação ponta a ponta real

## P1 — próximo ciclo (valor alto, esforço moderado)

1. **Canal do navegador na pesquisa automaticamente**: quando a busca web ou um site for
   bloqueado, criar pedido `abrir_url`+`ler_aba` para o usuário autorizar e o agente ler
   o resultado na aba autenticada (hoje o canal existe e é testado, mas a pesquisa não o
   invoca sozinha).
2. **Fontes públicas extras com licença clara**: CNPJ aberto (download da Receita) para
   descoberta por segmento/município; Overpass API como segunda fonte OSM (com backoff
   entre espelhos — estava sobrecarregada neste ambiente).
3. **Edição em lote de resultados**: descartar/etiquetar várias empresas; notas do vendedor.
4. ~~**Envio real supervisionado**~~ ✅ **feito (P1→P0)**: ação `enviar_email_mail` com
   aprovação hash-vinculada e botão dedicado na página da empresa. Restante de
   `mailto:`/anexos segue opcional.
5. **Agendamento de novas rodadas** por ICP (ex.: refazer pesquisa semanal e diff de novos
   candidatos), reutilizando o motor de tarefas.

## P2 — refinamentos

6. Selo de status da extensão atualizar o DOM só quando o texto mudar (reduz churn de
   layout observado na automação da UI).
7. Extração estruturada de contato (e-mail/telefone/whatsapp) por heurísticas de seção
   (“Fale conosco”) com evidência por campo.
8. Subpáginas na coleta (ex.: `/sobre`, `/contato`) quando o robots.txt permitir.
9. Internacionalização da interface (pt-BR já é o padrão; en como opção).
10. Empacotamento como app (menubar/plist) com ícone próprio e auto-início.

## P3 — exploratório

11. Modo “semi-autônomo” com orçamento de sites por dia e fila de aprovações offline.
12. Sincronização opcional com CRM (HubSpot/Pipedrive) — exige novo ADR de privacidade.
13. Scoring aprendido: usar aprovações/descartes do usuário para ajustar pesos por ICP.
