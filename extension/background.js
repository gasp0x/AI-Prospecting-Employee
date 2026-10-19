// Service worker da extensão — mantém o WebSocket com o app local (127.0.0.1)
// e executa pedidos SOMENTE após clique explícito de “Permitir” no popup.
//
// Privacidade:
// - lê apenas URL, título e texto visível da aba ativa autorizada (activeTab);
// - NUNCA acessa cookies, senhas, localStorage ou histórico;
// - nada sai da máquina: o WebSocket aponta para 127.0.0.1.

let ws = null;
let deveEstarConectado = false;
let pedidosPendentes = new Map(); // id -> {id, tipo, ...params}
let infoUltimaConexao = "";

const PADRAO_SERVIDOR = "ws://127.0.0.1:8734/ws/extensao";

// ---------- conexão ----------

async function conectar() {
  const { servidor, token } = await chrome.storage.local.get(["servidor", "token"]);
  const url = servidor || PADRAO_SERVIDOR;
  const urlFinal = token ? `${url}?token=${encodeURIComponent(token)}` : url;
  try {
    ws = new WebSocket(urlFinal);
  } catch (erro) {
    atualizarEstado({ conectado: false, erro: String(erro) });
    return;
  }
  ws.onopen = () => {
    infoUltimaConexao = new Date().toISOString();
    enviar({ ola: { navegador: agenteNavegador(), perfil: "perfil ativo do usuário", conectado_em: infoUltimaConexao } });
    atualizarEstado({ conectado: true, erro: "" });
    // entregar pendências locais (caso o servidor não as reenvie)
    for (const pedido of pedidosPendentes.values()) enviar({ pedido });
  };
  ws.onmessage = (evento) => {
    let mensagem;
    try { mensagem = JSON.parse(evento.data); } catch { return; }
    if (mensagem.pedido && mensagem.pedido.id != null) {
      pedidosPendentes.set(Number(mensagem.pedido.id), mensagem.pedido);
      atualizarBadge();
      notificarPopup();
    } else if (mensagem.pong) {
      /* heartbeat ok */
    }
  };
  ws.onclose = () => {
    ws = null;
    atualizarEstado({ conectado: false });
    if (deveEstarConectado) programarReconexao();
  };
  ws.onerror = () => { /* onclose cuida da reconexão */ };
}

function enviar(objeto) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(objeto));
    return true;
  }
  return false;
}

function agenteNavegador() {
  const ua = navigator.userAgent;
  if (ua.includes("Edg/")) return "Edge";
  if (ua.includes("OPR/")) return "Opera";
  if (ua.includes("Chrome/")) return "Chrome";
  if (ua.includes("Firefox/")) return "Firefox";
  return "desconhecido";
}

function programarReconexao() {
  chrome.alarms.create("reconectar", { delayInMinutes: 0.6 });
}

chrome.alarms.onAlarm.addListener((alarme) => {
  if (alarme.name === "reconectar" && deveEstarConectado && (!ws || ws.readyState > WebSocket.OPEN)) {
    conectar();
  }
});

// heartbeat a cada 20s mantém o service worker ativo enquanto conectado
chrome.alarms.create("heartbeat", { periodInMinutes: 0.35 });
chrome.alarms.onAlarm.addListener((alarme) => {
  if (alarme.name === "heartbeat" && ws && ws.readyState === WebSocket.OPEN) {
    enviar({ ping: Date.now() });
  }
});

// ---------- execução de pedidos (só com aprovação do popup) ----------

function extrairConteudoAba() {
  // roda NA PÁGINA (função serializada) — sem acesso a closures
  return {
    url: location.href,
    titulo: document.title,
    texto: (document.body && document.body.innerText ? document.body.innerText : "").slice(0, 50000),
    coletado_em: new Date().toISOString(),
  };
}

async function executarPedido(pedido) {
  if (pedido.tipo === "ler_aba") {
    const [aba] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!aba || !aba.id) throw new Error("nenhuma aba ativa");
    const [resultado] = await chrome.scripting.executeScript({
      target: { tabId: aba.id },
      func: extrairConteudoAba,
    });
    return resultado.result;
  }
  if (pedido.tipo === "abrir_url") {
    if (!/^https?:\/\//i.test(pedido.url || "")) throw new Error("URL inválida: " + pedido.url);
    const aba = await chrome.tabs.create({ url: pedido.url, active: false });
    return { aba_id: aba.id, url: pedido.url };
  }
  throw new Error("tipo de pedido desconhecido: " + pedido.tipo);
}

async function decidir(id, permitir) {
  const pedido = pedidosPendentes.get(Number(id));
  if (!pedido) return;
  pedidosPendentes.delete(Number(id));
  atualizarBadge();
  if (!permitir) {
    enviar({ resposta: { id: Number(id), ok: false, dados: { erro: "negado pelo usuário no popup" } } });
    notificarPopup();
    return;
  }
  try {
    const dados = await executarPedido(pedido);
    enviar({ resposta: { id: Number(id), ok: true, dados } });
  } catch (erro) {
    enviar({ resposta: { id: Number(id), ok: false, dados: { erro: String(erro && erro.message || erro) } } });
  }
  notificarPopup();
}

// ---------- estado/badge/popup ----------

function atualizarBadge() {
  const texto = pedidosPendentes.size ? String(pedidosPendentes.size) : "";
  chrome.action.setBadgeText({ text: texto });
  chrome.action.setBadgeBackgroundColor({ color: "#b45309" });
}

async function atualizarEstado(extra = {}) {
  const estado = { conectado: !!(ws && ws.readyState === WebSocket.OPEN), ...extra };
  await chrome.storage.local.set({ estadoExtensao: estado });
}

function notificarPopup() {
  chrome.runtime.sendMessage({ tipo: "mudou_estado" }).catch(() => { /* popup fechado */ });
}

// ---------- mensagens do popup ----------

chrome.runtime.onMessage.addListener((mensagem, _remetente, responder) => {
  (async () => {
    switch (mensagem && mensagem.tipo) {
      case "estado": {
        const { estadoExtensao } = await chrome.storage.local.get("estadoExtensao");
        responder({
          ...(estadoExtensao || { conectado: false }),
          pedidos: [...pedidosPendentes.values()],
        });
        break;
      }
      case "conectar":
        deveEstarConectado = true;
        await conectar();
        responder({ ok: true });
        break;
      case "desconectar":
        deveEstarConectado = false;
        if (ws) { try { ws.close(); } catch {} }
        responder({ ok: true });
        break;
      case "decidir":
        await decidir(mensagem.id, mensagem.permitir);
        responder({ ok: true });
        break;
      default:
        responder({ ok: false, erro: "mensagem desconhecida" });
    }
  })();
  return true; // resposta assíncrona
});

// se estava conectado antes do service worker dormir, reconecta ao acordar
chrome.runtime.onStartup.addListener(() => conectar());
chrome.runtime.onInstalled.addListener(() => conectar());
(async () => {
  const { estadoExtensao } = await chrome.storage.local.get("estadoExtensao");
  if (estadoExtensao && estadoExtensao.conectado) {
    deveEstarConectado = true;
    conectar();
  }
})();
