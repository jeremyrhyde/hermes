import pytest

from services.usage import ClaudeUsage


class FakeMessages:
    def __init__(self, fail: bool):
        self.fail = fail

    async def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("api down")
        return {"ok": True, **kwargs}


class FakeClient:
    def __init__(self, fail=False):
        self.messages = FakeMessages(fail)
        self.other = "passthrough"


async def test_wrapped_client_records_calls_and_errors():
    now = [1000.0]
    usage = ClaudeUsage(clock=lambda: now[0])
    good, bad = usage.wrap(FakeClient()), usage.wrap(FakeClient(fail=True))
    assert (await good.messages.create(model="m"))["model"] == "m"
    with pytest.raises(RuntimeError):
        await bad.messages.create(model="m")
    assert good.other == "passthrough"
    assert usage.counts() == (2, 1) and usage.last_call == 1000.0
    now[0] += 86_401
    assert usage.counts() == (0, 0)
