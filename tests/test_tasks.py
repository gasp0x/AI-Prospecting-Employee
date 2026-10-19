"""Testes do motor de tarefas: estados, cancelamento, prazo e falha."""
from app import db
from app.engine.tasks import TarefaCancelada, gerenciador_tarefas
from tests.conftest import aguardar


def _tipo_tarefa(tarefa_id):
    return db.buscar_um("tarefa", tarefa_id)["estado"]


def test_ciclo_completo_e_eventos(banco_tmp):
    def executar(ctx, params):
        ctx.progresso(50)
        ctx.evento("passo do meio", etapa="meio")
        return {"ok": True}

    tarefa_id = gerenciador_tarefas.criar("pesquisa", {})
    gerenciador_tarefas.iniciar(tarefa_id, executar)
    assert aguardar(lambda: _tipo_tarefa(tarefa_id) == "concluida")
    tarefa = db.buscar_um("tarefa", tarefa_id)
    assert tarefa["progresso"] == 100
    assert db.de_json(tarefa["resultado_json"]) == {"ok": True}
    etapas = [e["mensagem"] for e in db.listar("tarefa_evento", where="tarefa_id = ?", parametros=(tarefa_id,))]
    assert any("passo do meio" in m for m in etapas)
    assert any("iniciada" in m for m in etapas)


def test_cancelamento_no_ponto_de_verificacao(banco_tmp):
    import time

    def executar(ctx, params):
        for _ in range(100):
            ctx.check()
            time.sleep(0.05)

    tarefa_id = gerenciador_tarefas.criar("pesquisa", {})
    gerenciador_tarefas.iniciar(tarefa_id, executar)
    time.sleep(0.2)
    assert gerenciador_tarefas.cancelar(tarefa_id) is True
    assert aguardar(lambda: _tipo_tarefa(tarefa_id) == "cancelada")
    mensagens = [e["mensagem"] for e in db.listar("tarefa_evento", where="tarefa_id = ?", parametros=(tarefa_id,))]
    assert any("cancelada" in m.lower() for m in mensagens)


def test_tarefa_com_erro_vira_falha_com_historico(banco_tmp):
    def executar(ctx, params):
        raise ValueError("problema simulado de conector")

    tarefa_id = gerenciador_tarefas.criar("pesquisa", {})
    gerenciador_tarefas.iniciar(tarefa_id, executar)
    assert aguardar(lambda: _tipo_tarefa(tarefa_id) == "falha")
    tarefa = db.buscar_um("tarefa", tarefa_id)
    assert "problema simulado" in tarefa["erro"]
    eventos = db.listar("tarefa_evento", where="tarefa_id = ?", parametros=(tarefa_id,))
    assert any(e["nivel"] == "erro" for e in eventos)


def test_tempo_limite_expira_tarefa(banco_tmp):
    def executar(ctx, params):
        while True:
            ctx.check()
            import time

            time.sleep(0.05)

    tarefa_id = gerenciador_tarefas.criar("pesquisa", {})
    gerenciador_tarefas.iniciar(tarefa_id, executar, timeout_s=0.3)
    assert aguardar(lambda: _tipo_tarefa(tarefa_id) == "expirada")


def test_cancelar_tarefa_pendente_nao_iniciada(banco_tmp):
    tarefa_id = gerenciador_tarefas.criar("pesquisa", {})
    assert gerenciador_tarefas.cancelar(tarefa_id) is True
    assert _tipo_tarefa(tarefa_id) == "cancelada"


def test_excecao_cancelamento_importavel():
    # garante que a exceção existe e é exceção (uso nos executores)
    assert issubclass(TarefaCancelada, Exception)
