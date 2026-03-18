import asyncio

from llm_limiter import configure_llm_limit, llm_slot


def test_llm_slot_serializes_when_limit_is_one():
    async def _run():
        configure_llm_limit(1)
        order = []

        async def worker(name: str):
            async with llm_slot():
                order.append(f"start-{name}")
                await asyncio.sleep(0.02)
                order.append(f"end-{name}")

        await asyncio.gather(worker("a"), worker("b"))
        assert order[0] == "start-a"
        assert order[1] == "end-a"
        assert order[2] == "start-b"
        assert order[3] == "end-b"

    asyncio.run(_run())
