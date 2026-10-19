// Popup da extensão: conexão com o app local e decisão sobre pedidos.

const $servidor = document.getElementById("servidor");
const $token = document.getElementById("token");
const $status = document.getElementById("status");
const $lista = document.getElementById("lista-pedidos");

const DESCRICAO_TIPO = {
  ler_aba: "Ler a aba ativa (URL, título e texto visível)",
  abrir_url: "Abrir uma URL em nova aba",
};

function enviar(msg) {
  return new Promise((resposta) => chrome.runtime.sendMessage(msg, resposta));
}

function renderPedidos(pedidos) {
  if (!pedidos || !pedidos.length) {
    $lista.innerHTML = '<p class="vazio">Nenhum pedido pendente.</p>';
    return;
  }
  $lista.innerHTML = "";
  for (const pedido of pedidos) {
    const caixa = document.createElement("div");
    caixa.className = "pedido";
    const justificativa = pedido.justificativa ? `<div class="justificativa">${escapeHTML(pedido.justificativa)}</div>` : "";
    const url = pedido.url ? `<div class="justificativa">${escapeHTML(pedido.url)}</div>` : "";
    caixa.innerHTML = `
      <div class="tipo">#${pedido.id} · ${DESCRICAO_TIPO[pedido.tipo] || pedido.tipo}</div>
      ${justificativa}${url}
      <div class="acoes">
        <button class="permitir" data-id="${pedido.id}">Permitir</button>
        <button class="negar" data-id="${pedido.id}">Negar</button>
      </div>`;
    $lista.appendChild(caixa);
  }
  $lista.querySelectorAll(".permitir").forEach((b) =>
    b.addEventListener("click", () => enviar({ tipo: "decidir", id: Number(b.dataset.id), permitir: true }))
  );
  $lista.querySelectorAll(".negar").forEach((b) =>
    b.addEventListener("click", () => enviar({ tipo: "decidir", id: Number(b.dataset.id), permitir: false }))
  );
}

function escapeHTML(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

async function atualizar() {
  const estado = await enviar({ tipo: "estado" });
  if (estado.conectado) {
    $status.textContent = "conectado ao app local ✓";
    $status.className = "status ok";
  } else {
    $status.textContent = "desconectado — clique em Conectar com o app rodando";
    $status.className = "status falha";
  }
  renderPedidos(estado.pedidos);
}

(async () => {
  const { servidor, token } = await chrome.storage.local.get(["servidor", "token"]);
  $servidor.value = servidor || "ws://127.0.0.1:8734/ws/extensao";
  $token.value = token || "";
})();

document.getElementById("btn-conectar").addEventListener("click", async () => {
  await chrome.storage.local.set({ servidor: $servidor.value.trim(), token: $token.value.trim() });
  await enviar({ tipo: "conectar" });
  setTimeout(atualizar, 400);
});

document.getElementById("btn-desconectar").addEventListener("click", async () => {
  await enviar({ tipo: "desconectar" });
  setTimeout(atualizar, 300);
});

chrome.runtime.onMessage.addListener((mensagem) => {
  if (mensagem && mensagem.tipo === "mudou_estado") atualizar();
});

atualizar();
