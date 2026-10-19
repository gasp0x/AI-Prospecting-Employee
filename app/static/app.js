// Utilitários de interface: atualização periódica de status e polling de tarefas.

async function obterJSON(url) {
  const resposta = await fetch(url, { headers: { Accept: "application/json" } });
  if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
  return resposta.json();
}

function formatarData(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
  } catch { return iso; }
}

// ---- Painel/extensão: status do navegador a cada 4s ----
async function atualizarStatusExtensao() {
  const elemento = document.getElementById("status-extensao");
  if (!elemento) return;
  try {
    const status = await obterJSON("/api/extensao/status");
    if (status.conectado) {
      elemento.classList.add("conectado");
      elemento.classList.remove("desconectado");
      elemento.textContent = `⬤ navegador conectado (${status.navegador || "?"})`;
    } else {
      elemento.classList.add("desconectado");
      elemento.classList.remove("conectado");
      elemento.textContent = "○ navegador desconectado";
    }
  } catch { /* servidor indisponível: mantém último estado */ }
}
atualizarStatusExtensao();
setInterval(atualizarStatusExtensao, 4000);

// ---- Página de tarefa: progresso e eventos a cada 1.5s ----
const PAGINA_TAREFA = document.body.dataset.tarefaId;
if (PAGINA_TAREFA) {
  const ESTADOS_FINAIS = ["concluida", "falha", "cancelada", "expirada"];
  let timer = null;

  async function atualizarTarefa() {
    try {
      const estado = await obterJSON(`/api/tarefa/${PAGINA_TAREFA}/estado`);
      const barra = document.getElementById("barra-progresso");
      const rotulo = document.getElementById("rotulo-progresso");
      const badge = document.getElementById("badge-estado");
      if (barra) barra.style.width = `${estado.progresso}%`;
      if (rotulo) rotulo.textContent = `${estado.progresso}%`;
      if (badge) {
        badge.textContent = ROTULOS[estado.estado] || estado.estado;
        badge.className = `rotulo ${estado.estado}`;
      }
      const lista = document.getElementById("lista-eventos");
      if (lista) {
        lista.innerHTML = estado.eventos
          .map((e) => `<li class="${e.nivel}"><span class="ts">${formatarData(e.ts + "Z")}</span><span>${escapar(e.mensagem)}</span></li>`)
          .join("");
      }
      if (ESTADOS_FINAIS.includes(estado.estado)) {
        clearInterval(timer);
        // recarrega para exibir resultado final e links de resultados
        setTimeout(() => window.location.reload(), 800);
      }
    } catch { /* tenta de novo no próximo ciclo */ }
  }

  const ROTULOS = {
    pendente: "Pendente", em_execucao: "Em execução", aguardando_aprovacao: "Aguardando aprovação",
    concluida: "Concluída", falha: "Falha", cancelada: "Cancelada", expirada: "Expirada (tempo limite)",
  };

  function escapar(texto) {
    const div = document.createElement("div");
    div.textContent = texto ?? "";
    return div.innerHTML;
  }

  timer = setInterval(atualizarTarefa, 1500);
  atualizarTarefa();
}

// ---- Página de extensão: estado dos pedidos a cada 2s ----
const PAGINA_EXTENSAO = document.body.dataset.pagina === "extensao";
if (PAGINA_EXTENSAO) {
  setInterval(async () => {
    for (const span of document.querySelectorAll("[data-pedido-estado]")) {
      try {
        const pedido = await obterJSON(`/api/pedido/${span.dataset.pedidoEstado}`);
        span.textContent = ROTULO_PEDIDO[pedido.estado] || pedido.estado;
        span.className = `rotulo ${pedido.estado}`;
      } catch { /* ignora */ }
    }
  }, 2000);
  const ROTULO_PEDIDO = { pendente: "Pendente (aguardando você no popup da extensão)", atendida: "Atendida", negada: "Negada", expirada: "Expirada" };
}
