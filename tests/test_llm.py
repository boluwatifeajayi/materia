"""Provider adapter tests.

Translation both ways is checked without a network, so the shape of what we
send and what we read back is pinned regardless of whether a key is present.
One live Groq call exercises the real round trip.

The Anthropic adapter has no live test here. There is no ANTHROPIC_API_KEY in
this environment, so `claude-sonnet-5` is unverified and T19 to T21 depend on
it. That is recorded in the test at the bottom of this file rather than left
as a note somebody has to remember.
"""

import json
import os

import pytest

from materia.llm import (
    PROVIDERS,
    AnthropicClient,
    GroqClient,
    Message,
    ModelNotAvailable,
    ProviderError,
    ToolCall,
    ToolDefinition,
    get_client,
    read_provenance,
    write_provenance,
)
from materia.llm.anthropic import _translate_error as anthropic_error
from materia.llm.groq import _parse_arguments
from materia.llm.groq import _translate_error as groq_error

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


class TestAnthropicTranslation:
    def test_tools_use_an_input_schema(self):
        [tool] = AnthropicClient._tools([ADD])
        assert tool["name"] == "add_numbers"
        assert tool["input_schema"] == ADD.parameters
        assert "parameters" not in tool

    def test_tool_calls_become_tool_use_blocks(self):
        payload = AnthropicClient._messages(CONVERSATION)
        blocks = payload[1]["content"]
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["input"] == {"a": 17, "b": 25}

    def test_tool_results_become_user_blocks(self):
        payload = AnthropicClient._messages(CONVERSATION)
        assert payload[2]["role"] == "user"
        assert payload[2]["content"][0]["type"] == "tool_result"
        assert payload[2]["content"][0]["tool_use_id"] == "call_1"

    def test_consecutive_tool_results_share_one_turn(self):
        """Anthropic rejects two user turns in a row, so results from a
        parallel tool call have to be merged."""
        messages = [
            Message(role="user", content="go"),
            Message(
                role="assistant",
                tool_calls=(
                    ToolCall("a", "add_numbers", {}),
                    ToolCall("b", "add_numbers", {}),
                ),
            ),
            Message(role="tool", tool_call_id="a", content="1"),
            Message(role="tool", tool_call_id="b", content="2"),
        ]
        payload = AnthropicClient._messages(messages)
        assert len(payload) == 3
        assert len(payload[2]["content"]) == 2


class TestErrorTranslation:
    def test_an_unavailable_groq_model_is_its_own_error(self):
        error = groq_error(Exception("The model `x` does not exist"), "x")
        assert isinstance(error, ModelNotAvailable)

    def test_other_groq_failures_are_generic(self):
        error = groq_error(Exception("connection reset"), "x")
        assert isinstance(error, ProviderError)
        assert not isinstance(error, ModelNotAvailable)

    def test_an_unavailable_anthropic_model_says_not_to_guess(self):
        """Falling back to another model would produce a scored run against a
        model nobody chose, which is not a result."""
        error = anthropic_error(Exception("model_not_found"), "claude-sonnet-5")
        assert isinstance(error, ModelNotAvailable)
        assert "guess" in str(error)

    def test_a_missing_key_is_reported_before_any_request(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(ProviderError, match="GROQ_API_KEY"):
            GroqClient()

    def test_a_missing_anthropic_key_is_reported_too(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
            AnthropicClient()


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
        assert set(PROVIDERS) == {"groq", "anthropic"}

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

    def test_an_anthropic_run_is_marked_as_scored(self, tmp_path):
        class Scored:
            provider = "anthropic"
            model = "claude-sonnet-5"

        write_provenance(tmp_path, Scored())
        assert read_provenance(tmp_path)["scored"] is True

    def test_a_directory_with_no_record_reads_as_none(self, tmp_path):
        assert read_provenance(tmp_path) is None


@pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"), reason="needs GROQ_API_KEY"
)
class TestGroqLive:
    """One real call. The dev loop provider has to actually work."""

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


class TestTheAnthropicModelIsUnverified:
    """A standing reminder, kept in the suite rather than in a note.

    T19 to T21 are scored on Anthropic. Nothing has ever confirmed that
    `claude-sonnet-5` is a model the API will serve, because there is no key in
    this environment. This test passes while that is true and starts failing
    the moment a key appears, at which point the check should actually be run.
    """

    def test_run_the_live_check_once_a_key_exists(self):
        if os.environ.get("ANTHROPIC_API_KEY"):
            pytest.fail(
                "ANTHROPIC_API_KEY is now set. Run the live model id check "
                "before T19: python -m materia llm check --provider anthropic"
            )
        assert AnthropicClient.__init__ is not None  # the adapter is built and ready


class _Block:
    """A stand in for an SDK content block."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Reply:
    def __init__(self, content, stop_reason="end_turn", model="claude-sonnet-5"):
        self.content = content
        self.stop_reason = stop_reason
        self.model = model
        self.usage = _Usage(120, 45)


def _anthropic_client(monkeypatch, reply):
    """An AnthropicClient wired to a stub SDK.

    No key and no network. What this pins is the half of the adapter that runs
    after the API answers, which is where a scored run would break without
    anybody noticing until T19.
    """
    client = AnthropicClient.__new__(AnthropicClient)
    client.model = "claude-sonnet-5"

    class Messages:
        def __init__(self):
            self.captured = None

        def create(self, **request):
            self.captured = request
            if isinstance(reply, Exception):
                raise reply
            return reply

    class Sdk:
        def __init__(self):
            self.messages = Messages()

    sdk = Sdk()
    client._client = sdk
    return client, sdk


class TestAnthropicResponses:
    def test_text_and_tool_calls_are_normalised(self, monkeypatch):
        reply = _Reply(
            [
                _Block(type="text", text="Let me check that."),
                _Block(type="tool_use", id="tu_1", name="add_numbers", input={"a": 1}),
            ],
            stop_reason="tool_use",
        )
        client, _ = _anthropic_client(monkeypatch, reply)
        response = client.complete("s", [Message(role="user", content="go")], [ADD])

        assert response.provider == "anthropic"
        assert response.text == "Let me check that."
        assert response.tool_calls == (ToolCall("tu_1", "add_numbers", {"a": 1}),)
        assert response.stop_reason == "tool_use"
        assert response.usage.total == 165

    def test_a_reply_with_no_text_gives_none_not_an_empty_string(self, monkeypatch):
        reply = _Reply([_Block(type="tool_use", id="t", name="add_numbers", input={})])
        client, _ = _anthropic_client(monkeypatch, reply)
        assert client.complete("s", [Message(role="user", content="go")], [ADD]).text is None

    def test_multiple_text_blocks_are_joined(self, monkeypatch):
        reply = _Reply([_Block(type="text", text="one"), _Block(type="text", text="two")])
        client, _ = _anthropic_client(monkeypatch, reply)
        assert client.complete("s", [Message(role="user", content="go")]).text == "one\ntwo"

    def test_the_system_prompt_goes_in_its_own_field(self, monkeypatch):
        client, sdk = _anthropic_client(monkeypatch, _Reply([_Block(type="text", text="x")]))
        client.complete("be brief", [Message(role="user", content="go")], [ADD])
        assert sdk.messages.captured["system"] == "be brief"
        assert sdk.messages.captured["temperature"] == 0
        assert sdk.messages.captured["tools"][0]["name"] == "add_numbers"

    def test_no_tools_field_when_there_are_no_tools(self, monkeypatch):
        client, sdk = _anthropic_client(monkeypatch, _Reply([_Block(type="text", text="x")]))
        client.complete("s", [Message(role="user", content="go")])
        assert "tools" not in sdk.messages.captured

    def test_a_failure_is_normalised_into_a_provider_error(self, monkeypatch):
        client, _ = _anthropic_client(monkeypatch, RuntimeError("overloaded"))
        with pytest.raises(ProviderError, match="Anthropic request failed"):
            client.complete("s", [Message(role="user", content="go")])

    def test_an_unavailable_model_surfaces_as_such(self, monkeypatch):
        client, _ = _anthropic_client(monkeypatch, RuntimeError("model_not_found"))
        with pytest.raises(ModelNotAvailable):
            client.complete("s", [Message(role="user", content="go")])


class TestGroqFailures:
    def test_a_failure_is_normalised(self, monkeypatch):
        client = GroqClient.__new__(GroqClient)
        client.model = "openai/gpt-oss-120b"

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
