"""Provider adapter tests.

Translation both ways is checked without a network, so the shape of what we
send and what we read back is pinned regardless of whether a key is present.
One live Groq call exercises the real round trip.

The OpenAI adapter has no live test here. There is no OPENAI_API_KEY in this
environment, so `gpt-5.6-terra` is unverified and T19 to T21 depend on it. That is recorded in the test at the bottom of this file rather than left
as a note somebody has to remember.
"""

import json
import os

import pytest

from materia.llm import (
    PROVIDERS,
    GroqClient,
    OpenAIClient,
    Message,
    ModelNotAvailable,
    ProviderError,
    RateLimited,
    ToolCall,
    ToolDefinition,
    get_client,
    read_provenance,
    write_provenance,
)
from materia.llm.groq import _parse_arguments
from materia.llm.groq import _translate_error as groq_error
from materia.llm.openai_client import OpenAIClient as _OpenAIClient
from materia.llm.openai_compatible import OpenAICompatibleClient

ADD = ToolDefinition(
    name="add_numbers",
    description="Add two numbers together and return the sum.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
)

CONVERSATION = [
    Message(role="user", content="What is 17 plus 25?"),
    Message(
        role="assistant",
        content=None,
        tool_calls=(ToolCall(id="call_1", name="add_numbers", arguments={"a": 17, "b": 25}),),
    ),
    Message(role="tool", tool_call_id="call_1", content="42"),
]


class TestGroqTranslation:
    def test_tools_become_openai_functions(self):
        [tool] = GroqClient._tools([ADD])
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "add_numbers"
        assert tool["function"]["parameters"] == ADD.parameters

    def test_no_tools_translates_to_nothing(self):
        assert GroqClient._tools([]) is None

    def test_the_system_prompt_becomes_the_first_message(self):
        payload = GroqClient._messages("be brief", CONVERSATION)
        assert payload[0] == {"role": "system", "content": "be brief"}

    def test_tool_calls_carry_json_encoded_arguments(self):
        payload = GroqClient._messages("s", CONVERSATION)
        call = payload[2]["tool_calls"][0]
        assert call["id"] == "call_1"
        assert json.loads(call["function"]["arguments"]) == {"a": 17, "b": 25}

    def test_a_tool_result_references_the_call_it_answers(self):
        payload = GroqClient._messages("s", CONVERSATION)
        assert payload[3] == {"role": "tool", "tool_call_id": "call_1", "content": "42"}


class TestOpenAIUsesTheResponsesApi:
    """The plan was to point the Groq adapter elsewhere, and Groq does speak
    the chat wire format. But gpt-5.6-terra refuses function tools on
    /v1/chat/completions unless reasoning_effort is 'none', which gives up the
    reasoning this tier was chosen for. So the request and reply shapes differ
    and complete() is written rather than inherited."""

    def test_tools_are_flat_not_nested_under_a_function_key(self):
        """The chat API wants {"function": {...}}. The Responses API does not."""
        [tool] = OpenAIClient._tools([ADD])
        assert tool["name"] == "add_numbers"
        assert tool["parameters"] == ADD.parameters
        assert "function" not in tool

        [chat_tool] = GroqClient._tools([ADD])
        assert chat_tool["function"]["name"] == "add_numbers"

    def test_a_tool_call_and_its_result_become_two_linked_items(self):
        """Not a message carrying a list of calls. They are separate input
        items joined by call_id."""
        items = OpenAIClient._input(CONVERSATION)
        call = next(i for i in items if i.get("type") == "function_call")
        result = next(i for i in items if i.get("type") == "function_call_output")
        assert call["call_id"] == result["call_id"] == "call_1"
        assert json.loads(call["arguments"]) == {"a": 17, "b": 25}
        assert result["output"] == "42"

    def test_a_plain_turn_stays_a_role_and_content_item(self):
        items = OpenAIClient._input([Message(role="user", content="go")])
        assert items == [{"role": "user", "content": "go"}]

    def test_assistant_text_alongside_a_tool_call_is_kept(self):
        items = OpenAIClient._input([
            Message(role="assistant", content="Let me check.",
                    tool_calls=(ToolCall("c1", "add_numbers", {}),))
        ])
        assert items[0] == {"role": "assistant", "content": "Let me check."}
        assert items[1]["type"] == "function_call"

    def test_the_shared_half_is_still_shared(self):
        """Client construction, timeouts and key handling are not rewritten."""
        assert OpenAIClient.__init__ is OpenAICompatibleClient.__init__
        assert OpenAIClient._missing_key_message is OpenAICompatibleClient._missing_key_message

    def test_the_providers_differ_only_where_they_have_to(self):
        assert OpenAIClient.provider == "openai"
        assert OpenAIClient.API_KEY_VARIABLE == "OPENAI_API_KEY"
        assert OpenAIClient.BASE_URL is None  # the SDK default, api.openai.com
        assert GroqClient.BASE_URL.startswith("https://api.groq.com")

    def test_the_scored_provider_carries_no_pacer_by_default(self, monkeypatch):
        """The free Groq tier needs one. Pacing a limit that is not binding
        would add an hour of waiting to a run for nothing."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        assert OpenAIClient().pacer is None
        assert GroqClient().pacer is not None


class TestErrorTranslation:
    def test_an_unavailable_groq_model_is_its_own_error(self):
        error = groq_error(Exception("The model `x` does not exist"), "x")
        assert isinstance(error, ModelNotAvailable)

    def test_other_groq_failures_are_generic(self):
        error = groq_error(Exception("connection reset"), "x")
        assert isinstance(error, ProviderError)
        assert not isinstance(error, ModelNotAvailable)

    @staticmethod
    def _openai(model="gpt-5.6-terra"):
        client = _OpenAIClient.__new__(_OpenAIClient)
        client.model = model
        return client

    def test_an_unavailable_openai_model_says_not_to_guess(self):
        """Falling back to another model would produce a scored run against a
        model nobody chose, which is not a result."""
        error = self._openai()._translate_error(Exception("model_not_found"))
        assert isinstance(error, ModelNotAvailable)
        assert "guess" in str(error)

    def test_billing_refusals_are_not_mistaken_for_rate_limits(self):
        """They need different action: one is waiting, the other is paying."""
        error = self._openai()._translate_error(Exception("insufficient_quota"))
        assert not isinstance(error, RateLimited)
        assert "billing" in str(error)

    def test_a_missing_key_names_the_selected_provider(self, monkeypatch):
        """Found in a clean clone run. Following the guide and exporting only
        another provider's key produced "GROQ_API_KEY is not set", true and
        unhelpful: nothing said the provider defaults to groq."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("MATERIA_PROVIDER", raising=False)
        with pytest.raises(ProviderError) as raised:
            GroqClient()
        assert "MATERIA_PROVIDER" in str(raised.value)
        assert "the default" in str(raised.value)

    def test_a_missing_openai_key_names_the_variable_and_the_provider(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ProviderError) as raised:
            OpenAIClient()
        assert "OPENAI_API_KEY" in str(raised.value)
        assert "MATERIA_PROVIDER" in str(raised.value)


class TestArgumentParsing:
    def test_valid_json(self):
        assert _parse_arguments('{"a": 1}') == {"a": 1}

    def test_nothing(self):
        assert _parse_arguments(None) == {}

    def test_malformed_json_is_kept_rather_than_dropped(self):
        """A tool call the model got wrong is evidence about the model, so it
        is passed through where the caller can see it rather than swallowed."""
        assert _parse_arguments("{not json") == {"__unparsed__": "{not json"}

    def test_a_json_value_that_is_not_an_object(self):
        assert _parse_arguments("42") == {"__value__": 42}


class TestSelection:
    def test_both_providers_are_registered(self):
        assert set(PROVIDERS) == {"groq", "openai"}

    def test_anthropic_is_gone_rather_than_left_as_dead_code(self):
        import importlib

        assert "anthropic" not in PROVIDERS
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("materia.llm.anthropic")

    def test_an_unknown_provider_is_refused(self):
        with pytest.raises(ProviderError, match="unknown provider"):
            get_client("gemini")


class TestProvenance:
    """A stray dev loop run must never be mistaken for the scored one."""

    class _Fake:
        provider = "groq"
        model = "openai/gpt-oss-120b"

    def test_it_records_who_produced_a_results_directory(self, tmp_path):
        write_provenance(tmp_path, self._Fake())
        record = read_provenance(tmp_path)
        assert record["provider"] == "groq"
        assert record["model"] == "openai/gpt-oss-120b"

    def test_a_groq_run_is_marked_as_not_scored(self, tmp_path):
        write_provenance(tmp_path, self._Fake())
        assert read_provenance(tmp_path)["scored"] is False

    def test_an_openai_run_is_marked_as_scored(self, tmp_path):
        class Scored:
            provider = "openai"
            model = "gpt-5.6-terra"

        write_provenance(tmp_path, Scored())
        assert read_provenance(tmp_path)["scored"] is True

    def test_a_directory_with_no_record_reads_as_none(self, tmp_path):
        assert read_provenance(tmp_path) is None


@pytest.mark.skipif(
    not (os.environ.get("GROQ_API_KEY") and os.environ.get("MATERIA_LIVE_TESTS")),
    reason="set MATERIA_LIVE_TESTS=1 with a GROQ_API_KEY to run the live check",
)
class TestGroqLive:
    """One real call. The dev loop provider has to actually work.

    Opt in rather than automatic. docs/REPRODUCTION.md promises make verify
    needs no API key, and on the free tier this call also waits on the token
    pacer, which would make every test run a minute longer for no gain.
    `python -m materia llm check` is the same check on demand.
    """

    def test_a_tool_call_round_trips(self):
        client = GroqClient()
        response = client.complete(
            system="You are a calculator. Use the add_numbers tool for any arithmetic.",
            messages=[Message(role="user", content="What is 17 plus 25?")],
            tools=[ADD],
        )
        assert response.provider == "groq"
        assert response.tool_calls, "the model did not call the tool"
        call = response.tool_calls[0]
        assert call.name == "add_numbers"
        assert {call.arguments["a"], call.arguments["b"]} == {17, 25}
        assert response.usage.total > 0


class TestTheScoredModelIsUnverified:
    """A standing reminder, kept in the suite rather than in a note.

    T19 to T21 are scored on OpenAI. Nothing has confirmed that
    `gpt-5.6-terra` is a model the API will serve, because there is no key in
    this environment. This test passes while that is true and starts failing
    the moment a key appears, at which point the check should actually be run.
    """

    def test_run_the_live_check_once_a_key_exists(self):
        if os.environ.get("OPENAI_API_KEY"):
            pytest.fail(
                "OPENAI_API_KEY is now set. Run the live model id check before "
                "anything depends on it: "
                "python -m materia llm check --provider openai"
            )
        assert OpenAIClient.__init__ is not None  # the adapter is built and ready


class TestGroqFailures:
    def test_a_failure_is_normalised(self, monkeypatch):
        client = GroqClient.__new__(GroqClient)
        client.model = "openai/gpt-oss-120b"
        client.pacer = None

        class Sdk:
            class chat:  # noqa: N801
                class completions:  # noqa: N801
                    @staticmethod
                    def create(**_):
                        raise RuntimeError("connection reset")

        client._client = Sdk()
        with pytest.raises(ProviderError, match="Groq request failed"):
            client.complete("s", [Message(role="user", content="go")])


class TestAgentResponse:
    def test_it_knows_whether_it_wants_a_tool(self):
        from materia.llm import AgentResponse

        assert AgentResponse(text="x").wants_tools is False
        assert AgentResponse(
            text=None, tool_calls=(ToolCall("1", "t", {}),)
        ).wants_tools is True


class TestTokenPacing:
    """Groq's free tier caps tokens per minute. The pacer waits before a call
    that would break the cap rather than retrying after it is refused, which
    is what CLAUDE.md section 6 rules out: a retry spends quota on a request
    that was never going to succeed."""

    def test_it_does_not_wait_when_the_window_is_empty(self):
        from materia.llm import TokenPacer

        assert TokenPacer(8000).wait_for(2000) == 0.0

    def test_it_leaves_headroom_under_the_published_limit(self):
        """The limit counts the reply too, and the reply size is unknown when
        the request goes out."""
        from materia.llm import TokenPacer

        assert TokenPacer(8000).budget < 8000

    def test_it_waits_when_the_window_is_full(self, monkeypatch):
        """The clock is advanced by the fake sleep. Stubbing sleep alone would
        leave the pacer spinning against a real clock for a real minute, which
        is what a rate limiter is supposed to feel like and not what a test
        should."""
        from materia.llm import openai_compatible as pacing

        clock = [1000.0]
        slept: list[float] = []

        def fake_sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        monkeypatch.setattr(pacing.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(pacing.time, "sleep", fake_sleep)

        pacer = pacing.TokenPacer(8000)
        pacer.record(6000)
        waited = pacer.wait_for(2000)

        assert slept, "it should have waited"
        assert waited == sum(slept)
        assert waited < 120, "one window, not an unbounded spin"

    def test_spending_older_than_a_minute_stops_counting(self, monkeypatch):
        from materia.llm import openai_compatible as pacing

        clock = [1000.0]
        monkeypatch.setattr(pacing.time, "monotonic", lambda: clock[0])
        pacer = pacing.TokenPacer(8000)
        pacer.record(6000)
        clock[0] += 61
        assert pacer.wait_for(2000) == 0.0

    def test_it_waits_for_the_entry_that_frees_enough_not_the_oldest(self, monkeypatch):
        """An old entry that frees enough on its own should not make the
        caller wait behind a newer one. Over a long run that is minutes."""
        from materia.llm import openai_compatible as pacing

        clock = [1000.0]
        monkeypatch.setattr(pacing.time, "monotonic", lambda: clock[0])

        pacer = pacing.TokenPacer(8000)
        pacer.record(5000)  # 50 seconds ago by the time we ask
        clock[0] += 50
        pacer.record(1000)  # just now

        # 500 tokens of room needed, and the first entry alone frees 5000.
        pause = pacer._pause_until_room(clock[0], 500)
        assert 10 < pause < 12, pause  # the old entry ages out in about 11s

    def test_it_waits_for_the_whole_window_when_one_entry_is_not_enough(self, monkeypatch):
        from materia.llm import openai_compatible as pacing

        clock = [1000.0]
        monkeypatch.setattr(pacing.time, "monotonic", lambda: clock[0])

        pacer = pacing.TokenPacer(8000)
        pacer.record(1000)
        clock[0] += 50
        pacer.record(5000)

        pause = pacer._pause_until_room(clock[0], 5500)
        assert 60 < pause < 62, pause  # it has to wait for the newer entry too

    def test_a_request_larger_than_the_whole_budget_still_goes_through(self, monkeypatch):
        """Otherwise a single oversized request would hang the run forever."""
        from materia.llm import openai_compatible as pacing

        clock = [1000.0]
        slept = []
        monkeypatch.setattr(pacing.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(
            pacing.time, "sleep", lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s))
        )

        pacer = pacing.TokenPacer(1000)
        pacer.record(500)
        pacer.wait_for(10_000)
        assert slept, "it should have waited once"

    def test_an_empty_window_needs_no_wait_at_all(self):
        from materia.llm.groq import TokenPacer

        assert TokenPacer(8000)._pause_until_room(1000.0, 500) == 0.5

    def test_a_rate_limit_reply_gets_its_own_error_class(self):
        """So a caller can stop rather than hammer."""
        from materia.llm import RateLimited
        from materia.llm.groq import _translate_error

        error = _translate_error(Exception("rate_limit_exceeded"), "m")
        assert isinstance(error, RateLimited)

    def test_the_estimate_covers_the_request_and_a_reply(self):
        from materia.llm.groq import _estimate_tokens

        small = _estimate_tokens({"messages": [{"role": "user", "content": "hi"}]})
        large = _estimate_tokens({"messages": [{"role": "user", "content": "x" * 4000}]})
        assert small > 0
        assert large > small + 900


class _Choice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _GroqMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _GroqCall:
    def __init__(self, identifier, name, arguments):
        self.id = identifier
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class _Completion:
    def __init__(self, choices, model="openai/gpt-oss-120b"):
        self.choices = choices
        self.model = model
        self.usage = type("U", (), {"prompt_tokens": 151, "completion_tokens": 52,
                                    "total_tokens": 203})()


def _groq_client(reply):
    """A GroqClient wired to a stub SDK.

    This pins the half of the adapter that runs after the API answers, which
    the live test no longer covers now that it is opt in. Both providers share
    that code, so this covers the OpenAI path too.
    """
    client = GroqClient.__new__(GroqClient)
    client.model = "openai/gpt-oss-120b"
    client.pacer = None

    captured = {}

    class Completions:
        @staticmethod
        def create(**request):
            captured.update(request)
            return reply

    class Sdk:
        class chat:  # noqa: N801
            completions = Completions()

    client._client = Sdk()
    return client, captured


class TestGroqResponses:
    def test_a_tool_call_is_normalised(self):
        reply = _Completion(
            [
                _Choice(
                    _GroqMessage(
                        content=None,
                        tool_calls=[_GroqCall("fc_1", "add_numbers", '{"a": 17, "b": 25}')],
                    ),
                    finish_reason="tool_calls",
                )
            ]
        )
        client, _ = _groq_client(reply)
        response = client.complete("s", [Message(role="user", content="go")], [ADD])

        assert response.provider == "groq"
        assert response.tool_calls == (ToolCall("fc_1", "add_numbers", {"a": 17, "b": 25}),)
        assert response.stop_reason == "tool_calls"
        assert response.usage.total == 203

    def test_a_plain_reply_carries_its_text(self):
        client, _ = _groq_client(_Completion([_Choice(_GroqMessage(content="The sum is 42."))]))
        response = client.complete("s", [Message(role="user", content="go")])
        assert response.text == "The sum is 42."
        assert response.tool_calls == ()

    def test_the_request_is_deterministic_and_carries_the_tools(self):
        client, captured = _groq_client(_Completion([_Choice(_GroqMessage(content="x"))]))
        client.complete("be brief", [Message(role="user", content="go")], [ADD])
        assert captured["temperature"] == 0
        assert captured["messages"][0] == {"role": "system", "content": "be brief"}
        assert captured["tools"][0]["function"]["name"] == "add_numbers"

    def test_no_tools_field_when_there_are_none(self):
        client, captured = _groq_client(_Completion([_Choice(_GroqMessage(content="x"))]))
        client.complete("s", [Message(role="user", content="go")])
        assert "tools" not in captured

    def test_the_pacer_is_consulted_and_told_what_was_spent(self):
        from materia.llm import TokenPacer

        client, _ = _groq_client(_Completion([_Choice(_GroqMessage(content="x"))]))
        client.pacer = TokenPacer(8000)
        client.complete("s", [Message(role="user", content="go")])
        assert client.pacer._used(__import__("time").monotonic()) == 203

    def test_a_real_client_gets_a_pacer_by_default(self, monkeypatch):
        """The free tier limit is low enough that an unpaced client fails on
        its second call."""
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        assert GroqClient().pacer is not None


class TestRequestTimeouts:
    """A hung connection has to fail the run, not stall it.

    Observed live: one call sat for 26 minutes with no traffic and no way out,
    which is worse than an error because nothing says anything is wrong.
    """

    def test_the_groq_client_sets_one(self, monkeypatch):
        from materia.llm.groq import REQUEST_TIMEOUT_SECONDS

        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        client = GroqClient()
        assert client._client.timeout == REQUEST_TIMEOUT_SECONDS
        assert client._client.max_retries > 0

    def test_the_openai_client_sets_one(self, monkeypatch):
        from materia.llm.openai_compatible import REQUEST_TIMEOUT_SECONDS

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        client = OpenAIClient()
        assert client._client.timeout == REQUEST_TIMEOUT_SECONDS
        assert client._client.max_retries > 0

    def test_a_timeout_is_reported_as_a_provider_error(self):
        """Normalised like any other failure, so the loop above it does not
        have to know which SDK raised."""
        from materia.llm.groq import _translate_error

        assert isinstance(_translate_error(Exception("Request timed out."), "m"), ProviderError)


class TestTheOpenAIErrorBranches:
    """Each provider words a refusal differently, and the wrong reading sends
    a caller down the wrong path: waiting out a rate limit that is actually a
    wrong model id, or paying for quota when the model does not exist."""

    @staticmethod
    def _client():
        client = OpenAIClient.__new__(OpenAIClient)
        client.model = "gpt-5.6-terra"
        return client

    def test_a_rate_limit(self):
        error = self._client()._translate_error(Exception("Error code: 429 rate_limit"))
        assert isinstance(error, RateLimited)

    def test_anything_else_is_a_plain_provider_error(self):
        error = self._client()._translate_error(Exception("connection reset by peer"))
        assert isinstance(error, ProviderError)
        assert not isinstance(error, (RateLimited, ModelNotAvailable))
        assert "OpenAI request failed" in str(error)

    def test_a_deprecated_model_reads_as_unavailable(self):
        """A model that used to work and no longer does is the same problem as
        one that never existed: stop, do not substitute."""
        error = self._client()._translate_error(Exception("this model is deprecated"))
        assert isinstance(error, ModelNotAvailable)

    def test_the_base_class_refuses_to_guess(self):
        """A generic default would classify a wrong model id as a transient
        failure, which is the one mistake this class exists to prevent."""
        from materia.llm.openai_compatible import OpenAICompatibleClient

        base = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
        base.model = "x"
        with pytest.raises(NotImplementedError, match="words a refusal"):
            base._translate_error(Exception("anything"))


class TestGetClient:
    def test_it_builds_the_selected_provider(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        assert get_client("groq").provider == "groq"
        assert get_client("openai").provider == "openai"

    def test_it_falls_back_to_the_configured_default(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        assert get_client().provider == "groq"

    def test_a_model_can_be_named(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        assert get_client("openai", model="gpt-5.6-terra").model == "gpt-5.6-terra"


class _Item:
    """A stand in for a Responses API output item."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class _ResponsesReply:
    def __init__(self, output, output_text="", status="completed", model="gpt-5.6-terra"):
        self.output = output
        self.output_text = output_text
        self.status = status
        self.model = model
        self.usage = type("U", (), {"input_tokens": 75, "output_tokens": 33})()


def _openai_client(reply):
    """An OpenAIClient wired to a stub SDK.

    Pins the half that runs after the API answers. That half is not shared
    with Groq any more, so it needs its own cover.
    """
    client = OpenAIClient.__new__(OpenAIClient)
    client.model = "gpt-5.6-terra"
    client.pacer = None
    captured: dict = {}

    class Responses:
        @staticmethod
        def create(**request):
            captured.update(request)
            if isinstance(reply, Exception):
                raise reply
            return reply

    client._client = type("Sdk", (), {"responses": Responses()})()
    return client, captured


class TestOpenAIResponses:
    def test_a_function_call_is_normalised(self):
        reply = _ResponsesReply(
            [_Item(type="function_call", call_id="call_1", name="add_numbers",
                   arguments='{"a": 17, "b": 25}')]
        )
        client, _ = _openai_client(reply)
        response = client.complete("s", [Message(role="user", content="go")], [ADD])

        assert response.provider == "openai"
        assert response.tool_calls == (ToolCall("call_1", "add_numbers", {"a": 17, "b": 25}),)
        assert response.stop_reason == "tool_calls"
        assert response.usage.total == 108

    def test_a_text_reply_carries_its_text(self):
        client, _ = _openai_client(_ResponsesReply([_Item(type="message")], output_text="42"))
        response = client.complete("s", [Message(role="user", content="go")])
        assert response.text == "42"
        assert response.tool_calls == ()
        assert response.stop_reason == "completed"

    def test_an_empty_reply_gives_none_not_an_empty_string(self):
        client, _ = _openai_client(_ResponsesReply([], output_text="   "))
        assert client.complete("s", [Message(role="user", content="go")]).text is None

    def test_the_system_prompt_becomes_instructions(self):
        """Not a message with role system, which is the chat API's shape."""
        client, captured = _openai_client(_ResponsesReply([], output_text="x"))
        client.complete("be brief", [Message(role="user", content="go")], [ADD])
        assert captured["instructions"] == "be brief"
        assert all(item.get("role") != "system" for item in captured["input"])
        assert captured["tools"][0]["name"] == "add_numbers"

    def test_no_tools_field_when_there_are_none(self):
        client, captured = _openai_client(_ResponsesReply([], output_text="x"))
        client.complete("s", [Message(role="user", content="go")])
        assert "tools" not in captured

    def test_a_pacer_is_consulted_when_one_is_fitted(self):
        """None by default, since a paid account is not on a limit one request
        can exceed. An account that turns out to need one still gets paced."""
        from materia.llm import TokenPacer

        client, _ = _openai_client(_ResponsesReply([], output_text="x"))
        client.pacer = TokenPacer(8000)
        client.complete("s", [Message(role="user", content="go")])
        assert client.pacer._used(__import__("time").monotonic()) == 108

    def test_a_failure_is_normalised(self):
        client, _ = _openai_client(RuntimeError("connection reset"))
        with pytest.raises(ProviderError, match="OpenAI request failed"):
            client.complete("s", [Message(role="user", content="go")])

    def test_an_unavailable_model_surfaces_as_such(self):
        client, _ = _openai_client(RuntimeError("model_not_found"))
        with pytest.raises(ModelNotAvailable):
            client.complete("s", [Message(role="user", content="go")])


@pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") and os.environ.get("MATERIA_LIVE_TESTS")),
    reason="set MATERIA_LIVE_TESTS=1 with an OPENAI_API_KEY to run the live check",
)
class TestOpenAILive:
    """One real call against the scored provider.

    Opt in for the same reason as the Groq one: docs/REPRODUCTION.md promises
    make verify needs no API key. `python -m materia llm check --provider
    openai` is the same check on demand.
    """

    def test_a_tool_call_round_trips(self):
        client = OpenAIClient()
        response = client.complete(
            system="You are a calculator. Use the add_numbers tool for any arithmetic.",
            messages=[Message(role="user", content="What is 17 plus 25?")],
            tools=[ADD],
        )
        assert response.provider == "openai"
        assert response.tool_calls, "the model did not call the tool"
        call = response.tool_calls[0]
        assert call.name == "add_numbers"
        assert {call.arguments["a"], call.arguments["b"]} == {17, 25}


class TestTheAccountIdNeverReachesATrace:
    """Rate limit messages name the organisation the key belongs to.

    Not a credential, but trajectories are a published deliverable and there is
    no reason for an account identifier to be sitting in one.
    """

    MESSAGE = (
        "Error code: 429 - Rate limit reached for model `x` in organization "
        "`org_01m16gh92wefbbd5r1rt6hzy8b` service tier `on_demand`"
    )

    def test_groq_strips_it(self):
        from materia.llm.groq import GroqClient

        client = GroqClient.__new__(GroqClient)
        client.model = "m"
        translated = str(client._translate_error(Exception(self.MESSAGE)))
        assert "org_01m16gh92wefbbd5r1rt6hzy8b" not in translated
        assert "org_[redacted]" in translated
        assert "Rate limit reached" in translated

    def test_openai_strips_it(self):
        from materia.llm.openai_client import OpenAIClient

        client = OpenAIClient.__new__(OpenAIClient)
        client.model = "m"
        translated = str(client._translate_error(Exception(self.MESSAGE)))
        assert "org_01m16gh92wefbbd5r1rt6hzy8b" not in translated
        assert "org_[redacted]" in translated

    def test_it_leaves_everything_else_alone(self):
        from materia.llm.openai_compatible import scrub

        assert scrub("no account id here") == "no account id here"
        assert scrub("organization or org_short") == "organization or org_short"

    def test_no_committed_trajectory_carries_one(self):
        import re
        from pathlib import Path

        pattern = re.compile(r"\borg_[A-Za-z0-9]{8,}")
        offenders = [
            str(p) for p in Path("trajectories").rglob("*.jsonl")
            if pattern.search(p.read_text())
        ]
        assert offenders == []
