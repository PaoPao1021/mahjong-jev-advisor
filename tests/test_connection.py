import pytest
from mahjong_jev_advisor.jev import ModelConnection, JevError, JevAuthError, test_connection as probe
from mahjong_jev_advisor.settings import Settings


@pytest.mark.parametrize("protocol", ["vercel", "typesafe", "openrouter", "openai"])
def test_actual_http_request_and_parsing(http_server, protocol):
    if protocol == "openai":
        http_server.reply = {"model": "test-model", "choices": [{"message": {"content": '{"choice":"a0"}'}}]}
    config = ModelConnection(http_server.endpoint, "test-model", protocol)
    advice = probe("test-credential", config)
    path, headers, body = http_server.received[0]
    headers = {key.lower(): value for key, value in headers.items()}
    assert path == "/inference"
    assert headers["authorization"] == "Bearer test-credential"
    assert advice.selected.label == "连接成功"
    assert advice.source == "jev"
    if protocol == "vercel":
        assert headers["ai-model-id"] == "test-model"
        assert headers["ai-gateway-protocol-version"] == "0.0.1"
        assert "questions" in body and "model" not in body
    elif protocol in ("typesafe", "openrouter"):
        assert body["model"] == "test-model" and "questions" in body
    else:
        assert body["model"] == "test-model" and body["stream"] is False
        assert body["messages"][1]["role"] == "user"
        assert advice.probabilities == {} and advice.confidence is None


def test_real_http_billing_error_is_not_key_error(http_server):
    http_server.status = 403
    http_server.reply = {"error": {"type": "customer_verification_required", "message": "requires a valid credit card"}}
    with pytest.raises(JevAuthError, match="账单验证"):
        probe("test-credential", ModelConnection(http_server.endpoint))


def test_http_200_is_not_success_without_valid_choice(http_server):
    http_server.reply["answers"]["action"]["choice"] = "invented-action"
    with pytest.raises(JevError, match="候选列表以外"):
        probe("test-credential", ModelConnection(http_server.endpoint))


def test_custom_configuration_survives_restart():
    settings = Settings(api_key="test", model_endpoint="https://example.com/custom", model_name="custom",
                        model_protocol="openai", model_timeout=8.5)
    settings.save()
    restored = Settings.load()
    assert restored.api_key == "test"
    assert restored.connection() == settings.connection()


@pytest.mark.parametrize('failures', [1, 2, 3])
def test_transient_advice_retries_are_bounded(monkeypatch, failures):
    from mahjong_jev_advisor import jev
    from mahjong_jev_advisor.rules import candidates
    from mahjong_jev_advisor.state import GameState
    state = GameState.from_dict({'hand': '123m456p789s123z55m'})
    calls = []
    def post(*args):
        calls.append(1)
        if len(calls) <= failures:
            raise jev.JevUnavailableError('temporary')
        return {'answers': {'action': {'type': 'choice', 'choice': 'a0'}}}, 1
    monkeypatch.setattr(jev, '_post', post)
    monkeypatch.setattr(jev.time, 'sleep', lambda _: None)
    if failures == 3:
        with pytest.raises(jev.JevUnavailableError):
            jev.ask_jev(state, candidates(state), 'test')
    else:
        assert jev.ask_jev(state, candidates(state), 'test').source == 'jev'
    assert len(calls) == min(failures + 1, 3)


def test_auth_failure_is_not_retried(monkeypatch):
    from mahjong_jev_advisor import jev
    from mahjong_jev_advisor.rules import candidates
    from mahjong_jev_advisor.state import GameState
    state = GameState.from_dict({'hand': '123m456p789s123z55m'})
    calls = []
    def post(*args):
        calls.append(1)
        raise jev.JevAuthError('invalid key')
    monkeypatch.setattr(jev, '_post', post)
    with pytest.raises(jev.JevAuthError):
        jev.ask_jev(state, candidates(state), 'test')
    assert len(calls) == 1


def test_deep_analysis_transient_retry(monkeypatch):
    from mahjong_jev_advisor import llm
    calls = []
    def query(*args):
        calls.append(1)
        if len(calls) == 1:
            raise llm.LLMUnavailableError('temporary')
        return '分析成功'
    monkeypatch.setattr(llm, '_query_llm_analysis_once', query)
    monkeypatch.setattr(llm.time, 'sleep', lambda _: None)
    assert llm.query_llm_analysis(None, None, Settings()) == '分析成功'
    assert len(calls) == 2


def test_real_http_transient_failure_recovers_without_manual_retry(http_server, monkeypatch):
    from mahjong_jev_advisor import jev
    from mahjong_jev_advisor.rules import candidates
    from mahjong_jev_advisor.state import GameState
    state = GameState.from_dict({'hand': '123m456p789s123z55m'})
    http_server.status = 503
    def recover(_):
        http_server.status = 200
    monkeypatch.setattr(jev.time, 'sleep', recover)
    result = jev.ask_jev(state, candidates(state), 'test',
                         connection=ModelConnection(http_server.endpoint))
    assert result.source == 'jev'
    assert len(http_server.received) == 2


@pytest.mark.parametrize('engine', ['jev', 'llm'])
def test_interrupted_http_body_is_transient(monkeypatch, engine):
    import http.client
    from mahjong_jev_advisor import jev, llm
    from mahjong_jev_advisor.rules import candidates
    from mahjong_jev_advisor.state import GameState
    state = GameState.from_dict({'hand': '123m456p789s123z55m'})
    class BrokenResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, *args):
            raise http.client.IncompleteRead(b'partial', 100)
    if engine == 'jev':
        class Opener:
            def open(self, *args, **kwargs):
                return BrokenResponse()
        monkeypatch.setattr(jev.urllib.request, 'build_opener', lambda *args: Opener())
        with pytest.raises(jev.JevUnavailableError):
            jev._post({}, 'test', ModelConnection())
    else:
        monkeypatch.setattr(llm.urllib.request, 'urlopen', lambda *args, **kwargs: BrokenResponse())
        with pytest.raises(llm.LLMUnavailableError):
            llm._query_llm_analysis_once(state, candidates(state)[0], Settings(llm_api_key='test'))
