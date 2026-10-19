# Testes essenciais e evidências de validação

## Como rodar

```bash
.venv/bin/python -m pytest tests/ -q     # 40 testes, ~4 s, sem rede (herméticos)
```

## O que a suíte cobre

| Arquivo | Cobre |
|---|---|
| `test_icp.py` | texto livre → critérios (segmento com plural, localização, porte, palavras-chave, alertas, consultas, pesos) |
| `test_evidence.py` | HTML→texto, metadados (título, description, CNPJ, CEP, telefone), evidências com trecho original + URL + data, janelas sem duplicatas, hash |
| `test_scoring.py` | pontuação explicável: atendido (corroborado), parcial (só fonte de descoberta, 0,7×), inferido (0,5×), ausente; natureza fato/inferência na explicação |
| `test_tasks.py` | estados de tarefa, progresso, histórico, cancelamento no ponto seguro, falha com traceback, expiração por tempo limite, cancelamento de tarefa pendente |
| `test_approvals.py` | aprovação vinculada ao conteúdo exato (SHA-256), execução só após aprovar, **adulteração de conteúdo ⇒ falha (“hash não confere”)**, negação não executa, decisão única |
| `test_conectores.py` | detecção de bloqueio anti-bot do DDG com **fixture real capturada neste ambiente** (HTTP 202/anomaly), extração de links orgânicos descartando anúncios, robots.txt respeitado, CAPTCHA (Cloudflare) ⇒ `BloqueioDetectado`, coleta normal, heurística de site oficial, normalização de domínio |
| `test_extensao_protocolo.py` | protocolo WebSocket da extensão: hello/pong, criação e entrega de pedido, resposta “atendida” com conteúdo da aba, negação, backlog na reconexão, expiração, token inválido |
| `test_fluxo_e2e.py` | fluxo ponta a ponta hermético pela API HTTP: ICP → pesquisa (conectores simulados) → 2 empresas com pontuações esperadas (100,0 e 58,9) → limitação de busca registrada → rascunho → aprovação com hash → exportação CSV/JSON → persistência; duplicatas por domínio entre ICPs; páginas principais 200 |

## Evidências da validação real (neste ambiente, 2026-10-19)

Execuções reais contra fontes públicas, registradas no banco local e na auditoria:

1. **Ação local no macOS**: aprovação #1 (conteúdo exato + SHA-256 `24cbe3c0…`) → tarefa
   `executar:criar_nota_notes` concluída → nota criada no Apple Notes
   (`x-coredata://…/ICNote/p6`) e conferida no app (“MVP — teste de ação local”).
2. **ICP 1 — “clínicas de fisioterapia em Campinas (SP)”**: tarefa #1 concluída,
   **10 empresas reais, 10 qualificadas**, ex.:
   - Clínica Dragão Divino — **93,7** — site oficial `dragaodivino.com.br` (heurística de
     domínio ✓), evidências do site + categoria OSM + endereço;
   - Clínica Respirare — **84,2** — `clinicarespirare.com.br`;
   - 8 demais — 58,9 (comprovadas só pela fonte de descoberta OSM ⇒ status “parcial”).
3. **ICP 2 — “academias em São Paulo (SP)”**: tarefa #2 concluída, **10 empresas reais
   qualificadas** (43,7 cada — fonte de descoberta + busca web bloqueada registrada).
4. **Bloqueio real tratado**: durante a tarefa #3, `clinicacaparica.com.br` devolveu
   página de challenge — evento “Acesso bloqueado … interrompendo a coleta deste site”.
5. **Cancelamento ao vivo**: tarefa #3 cancelada aos 65%; estado final `cancelada`,
   progresso congelado e **13 empresas já coletadas preservadas**.
6. **Persistência**: servidor reiniciado; `/resultados` seguiu listando as 21 empresas
   (links verificados) e a exportação CSV (6.4 KB) / JSON (com fontes e evidências)
   continuou funcionando.
7. **Interface verificada visualmente** (capturas em `gui-test-screenshots/` durante o
   desenvolvimento): painel, resultados, detalhe da empresa com explicação da pontuação,
   tarefa cancelada com histórico, navegador/extensão e auditoria.

## Lacunas de teste conhecidas

- Não há teste de integração com o Chrome real (extensão carregada) — o protocolo é
  coberto por teste próprio; validação manual documentada em `docs/limitacoes.md`.
- Não há teste contra o Apple Mail (apenas Notes); o caminho é o mesmo executor com
  another adaptador `osascript`.
- Testes de rede ao vivo (Nominatim/BrasilAPI reais) ficaram fora do pytest para não
  dependerem de internet — foram validados manualmente nesta sessão e podem ser
  adicionados com marcação `@pytest.mark.live`.
