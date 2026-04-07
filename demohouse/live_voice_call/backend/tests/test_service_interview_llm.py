import asyncio

from arkitect.core.component.llm.model import ArkMessage

from interview_flow import ASK_CLARIFY, InterviewFlow, QuestionAnswerSnapshot
from interview_judge import Decision
from prompt import INTERVIEWER_SYSTEM_PROMPT
import service


class _FakeResponsesAdapter:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    async def stream_text(self, **kwargs):
        self.calls.append(kwargs)
        for chunk in self.chunks:
            yield chunk


def test_stream_interview_llm_chat_uses_responses_adapter_and_persists_history():
    async def _run():
        emitted = []
        fake_adapter = _FakeResponsesAdapter(["你好", "，请继续。"])
        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            llm2_thinking_type="enabled",
            llm2_reasoning_effort="low",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
            responses_adapter=fake_adapter,
            on_bot_sentence=emitted.append,
            session_id="s1",
        )
        svc.history_messages = [
            ArkMessage(**{"role": "assistant", "content": "上一轮问题"})
        ]
        chunks = []
        async for chunk in svc.stream_interview_llm_chat("请追问一个细节"):
            chunks.append(chunk)

        assert "".join(chunks) == "你好，请继续。"
        assert emitted[-1] == "你好，请继续。"
        assert fake_adapter.calls
        call = fake_adapter.calls[0]
        assert call["model"] == "ep-interviewer"
        assert call["thinking_type"] == "enabled"
        assert call["reasoning_effort"] == "low"
        assert svc.history_messages[-1].role == "assistant"
        assert svc.history_messages[-1].content == "你好，请继续。"

    asyncio.run(_run())


def test_build_interview_score_inputs_aggregates_scene_segment_fields():
    class _FakeFlow:
        def export_question_answer_snapshots(self):
            return [
                QuestionAnswerSnapshot(
                    question_id="q1",
                    sort_order=1,
                    question="请介绍项目",
                    evidence={
                        "ability_dimension": "项目管理",
                        "scoring_boundary": "目标-行动-结果",
                        "best_standard": "完整闭环",
                        "medium_standard": "部分闭环",
                        "worst_standard": "无结果",
                        "score_format": "评分0-5",
                        "comment_requirement": "摘要 + 改进建议",
                    },
                    candidate_answers=["我负责拆解目标", "最终提升转化率"],
                    aggregated_answer="我负责拆解目标\\n最终提升转化率",
                )
            ]

    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_mode=True,
    )
    svc.interview_flow = _FakeFlow()
    payloads = svc.build_interview_score_inputs()
    assert len(payloads) == 1
    item = payloads[0]
    assert item["question_id"] == "scene-1"
    assert item["ability_dimension"] == "项目管理"
    assert item["score_format"] == "评分0-5"
    assert item["comment_requirement"] == "摘要 + 改进建议"
    assert item["aggregated_answer"] == "我负责拆解目标\n最终提升转化率"
    assert item["question"].startswith("请介绍项目")


def test_build_interview_score_inputs_groups_by_contiguous_scene_segments():
    class _FakeFlow:
        def export_question_answer_snapshots(self):
            return [
                QuestionAnswerSnapshot(
                    question_id="q1",
                    sort_order=1,
                    question="项目背景是什么？",
                    evidence={"scenario": "项目复盘", "scoring_boundary": "关注背景", "score_format": "12"},
                    candidate_answers=["回答1"],
                    aggregated_answer="回答1",
                ),
                QuestionAnswerSnapshot(
                    question_id="q2",
                    sort_order=2,
                    question="你的职责是什么？",
                    evidence={"scenario": "项目复盘"},
                    candidate_answers=["回答2"],
                    aggregated_answer="回答2",
                ),
                QuestionAnswerSnapshot(
                    question_id="q3",
                    sort_order=3,
                    question="如何止血？",
                    evidence={"scenario": "线上故障", "scoring_boundary": "关注排障", "score_format": "10"},
                    candidate_answers=["回答3"],
                    aggregated_answer="回答3",
                ),
                QuestionAnswerSnapshot(
                    question_id="q4",
                    sort_order=4,
                    question="再次出现如何预防？",
                    evidence={"scenario": "线上故障"},
                    candidate_answers=["回答4"],
                    aggregated_answer="回答4",
                ),
                QuestionAnswerSnapshot(
                    question_id="q5",
                    sort_order=5,
                    question="再次谈项目复盘",
                    evidence={"scenario": "项目复盘", "scoring_boundary": "关注复盘深度", "score_format": "8"},
                    candidate_answers=["回答5"],
                    aggregated_answer="回答5",
                ),
            ]

    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_mode=True,
    )
    svc.interview_flow = _FakeFlow()

    payloads = svc.build_interview_score_inputs()
    assert [item["question_id"] for item in payloads] == ["scene-1", "scene-2", "scene-3"]
    assert payloads[0]["aggregated_answer"] == "回答1\n回答2"
    assert payloads[0]["score_format"] == "12"
    assert payloads[1]["aggregated_answer"] == "回答3\n回答4"
    assert payloads[1]["score_format"] == "10"
    assert payloads[2]["aggregated_answer"] == "回答5"
    assert payloads[2]["score_format"] == "8"


def test_stream_interview_llm_chat_uses_contiguous_scene_context_windows():
    async def _run():
        fake_adapter = _FakeResponsesAdapter(["好的。"])
        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
            interview_mode=True,
            interview_questions=[
                {"question_id": "q1", "main_question": "Q1", "scenario": "项目复盘"},
                {"question_id": "q2", "main_question": "Q2", "scenario": "项目复盘"},
                {"question_id": "q3", "main_question": "Q3", "scenario": "故障处理"},
            ],
            responses_adapter=fake_adapter,
            session_id="s1",
        )
        await svc.init()

        svc._activate_context_segment("q1")
        svc._append_history_message("user", "A1")
        svc._append_history_message("assistant", "B1")
        svc._activate_context_segment("q2")
        svc._append_history_message("user", "A2")

        svc._activate_context_segment("q3")
        svc._append_history_message("user", "C1")
        chunks = []
        async for chunk in svc.stream_interview_llm_chat("请继续提问"):
            chunks.append(chunk)
        assert "".join(chunks) == "好的。"

        third_scene_call = fake_adapter.calls[-1]
        third_scene_messages = third_scene_call["messages"]
        third_scene_texts = [item["content"] for item in third_scene_messages]
        assert "C1" in third_scene_texts
        assert "A1" not in third_scene_texts
        assert "A2" not in third_scene_texts
        assert third_scene_texts[-1] == "请继续提问"

        svc._activate_context_segment("q2")
        chunks = []
        async for chunk in svc.stream_interview_llm_chat("继续项目问题"):
            chunks.append(chunk)
        assert "".join(chunks) == "好的。"
        project_scene_call = fake_adapter.calls[-1]
        project_scene_texts = [item["content"] for item in project_scene_call["messages"]]
        assert "A1" in project_scene_texts
        assert "B1" in project_scene_texts
        assert "A2" in project_scene_texts
        assert "C1" not in project_scene_texts
        assert project_scene_texts[-1] == "继续项目问题"

    asyncio.run(_run())


def test_stream_interview_llm_chat_treats_empty_scene_as_per_question_window():
    async def _run():
        fake_adapter = _FakeResponsesAdapter(["收到。"])
        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
            interview_mode=True,
            interview_questions=[
                {"question_id": "q1", "main_question": "Q1", "scenario": ""},
                {"question_id": "q2", "main_question": "Q2", "scenario": ""},
            ],
            responses_adapter=fake_adapter,
            session_id="s2",
        )
        await svc.init()

        svc._activate_context_segment("q1")
        svc._append_history_message("user", "Q1-answer")
        svc._activate_context_segment("q2")
        svc._append_history_message("user", "Q2-answer")
        chunks = []
        async for chunk in svc.stream_interview_llm_chat("继续第二题"):
            chunks.append(chunk)
        assert "".join(chunks) == "收到。"

        call = fake_adapter.calls[-1]
        texts = [item["content"] for item in call["messages"]]
        assert "Q2-answer" in texts
        assert "Q1-answer" not in texts
        assert texts[-1] == "继续第二题"

    asyncio.run(_run())


def test_build_interview_context_forced_move_same_scene_uses_neutral_bridge_without_next_question():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    decision = Decision(
        next_action="next_question",
        next_prompt="",
        reason="follow_up_limit_reached",
        coverage_score=0.31,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup="请你介绍一个你主导的项目。",
        flow_state=service.ASK_QUESTION,
        is_scene_transition=False,
    )

    assert "中性衔接，不评价回答质量，不夸赞" in context
    assert "不要说“进入下一题”" in context
    assert "候选人回答已覆盖关键点" not in context
    assert "[过渡强度] soft" in context
    assert "当前为soft过渡" not in context


def test_build_interview_context_forced_move_cross_scene_allows_neutral_scene_switch():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    decision = Decision(
        next_action="next_question",
        next_prompt="",
        reason="global_turn_limit_reached",
        coverage_score=0.31,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup="请你介绍一个你主导的项目。",
        flow_state=service.ASK_QUESTION,
        is_scene_transition=True,
    )

    assert "中性过渡" in context
    assert "切换到新场景" in context
    assert "进入下一个场景" in context
    assert "候选人回答已覆盖关键点" not in context
    assert "[过渡强度] strong" in context


def test_build_interview_context_semantic_enough_same_scene_uses_ack_without_next_question():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    decision = Decision(
        next_action="next_question",
        next_prompt="",
        reason="semantic_enough_coverage",
        coverage_score=0.86,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup="请你讲讲最困难的一次技术决策。",
        flow_state=service.ASK_QUESTION,
        is_scene_transition=False,
    )

    assert "候选人回答已覆盖关键点，可用一句简短肯定后直接承接当前场景的后续问题" in context
    assert "不要说“进入下一题”" in context
    assert "中性过渡，不评价回答质量，不夸赞" not in context
    assert "[过渡强度] soft" in context
    assert "当前为soft过渡" in context


def test_build_interview_context_semantic_enough_cross_scene_allows_scene_switch():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    decision = Decision(
        next_action="next_question",
        next_prompt="",
        reason="semantic_enough_coverage",
        coverage_score=0.86,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup="请你讲讲最困难的一次技术决策。",
        flow_state=service.ASK_QUESTION,
        is_scene_transition=True,
    )

    assert "候选人回答已覆盖关键点，可用一句简短肯定后切换到下一个场景" in context
    assert "中性过渡，不评价回答质量，不夸赞" not in context
    assert "[过渡强度] strong" in context
    assert "当前为strong过渡" in context


def test_build_interview_context_followup_injects_expression_style():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    decision = Decision(
        next_action="follow_up",
        next_prompt="请你展开说一下关键决策依据。",
        reason="semantic_need_more_detail",
        coverage_score=0.45,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup="请你展开说一下关键决策依据。",
        flow_state=service.ASK_FOLLOWUP,
    )

    assert "[追问方向]" in context
    assert "[过渡强度] soft" in context
    assert "当前为soft过渡" in context


def test_build_interview_context_without_candidate_speech_disables_transition_styles():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )
    decision = Decision(
        next_action="next_question",
        next_prompt="",
        reason="semantic_enough_coverage",
        coverage_score=0.8,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup="请你介绍一个你主导的项目。",
        flow_state=service.ASK_QUESTION,
        has_candidate_speech=False,
    )

    assert "[过渡强度] none" in context
    assert "[首轮首问约束]" in context
    assert "禁止使用“进入下一题”“下一个场景”“我们继续”等过渡口吻" in context
    assert "当前为soft过渡" not in context
    assert "当前为strong过渡" not in context


def test_build_interview_context_clarify_uses_clarify_instruction():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    decision = Decision(
        next_action="clarify",
        next_prompt="这道题是希望你结合真实项目说明个人行动和结果。",
        reason="candidate_requested_clarification",
        coverage_score=0.12,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup="好的，我解释一下这道题。",
        flow_state=service.ASK_CLARIFY,
    )

    assert "仅做题意澄清，不要新增考察维度，也不要转成追问" in context
    assert "[澄清说明]" in context
    assert "复述题干关键点 -> 一句解释 -> 确认问句" in context


def test_sanitize_initial_question_text_removes_transition_prefix():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )
    raw = "好的我们先进入下一题，请你介绍一个你主导的项目。"
    cleaned = svc._sanitize_initial_question_text(raw)
    assert cleaned == "请你介绍一个你主导的项目。"


def test_sanitize_initial_question_text_keeps_non_transition_question():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )
    raw = "请你介绍一个你主导的项目。"
    cleaned = svc._sanitize_initial_question_text(raw)
    assert cleaned == raw


def test_build_interview_context_wrap_up_still_works():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    context = svc._build_interview_context(
        decision=None,
        next_question_or_followup="",
        flow_state=service.WRAP_UP,
    )

    assert "[指令] 面试即将结束，请做结束语。" in context


def test_interviewer_system_prompt_contains_question_fidelity_rules():
    assert "保真改写协议（最高优先级）" in INTERVIEWER_SYSTEM_PROMPT
    assert (
        "信息点必须全保留：主体、场景背景、前提条件、动作要求、全部数字与单位、阈值、比较关系、因果关系"
        in INTERVIEWER_SYSTEM_PROMPT
    )
    assert "禁止只保留末尾问题句（如“你更站哪边？”）" in INTERVIEWER_SYSTEM_PROMPT
    assert "对立观点/立场对照类前置背景（如“有人说…也有人说…”）属于必保留信息" in INTERVIEWER_SYSTEM_PROMPT
    assert "示例：错误“你更站哪边？”，正确“有人说…也有人说…，你更站哪边？”" in INTERVIEWER_SYSTEM_PROMPT
    assert "当口语化与保真冲突时，始终保真优先" in INTERVIEWER_SYSTEM_PROMPT
    assert "执行流程（仅内部思考，不要输出这些步骤）" in INTERVIEWER_SYSTEM_PROMPT
    assert "先抽取信息点清单，再口语化改写，最后逐项自检" in INTERVIEWER_SYSTEM_PROMPT
    assert "若漏任一点，回退为完整复述[下一步内容]" in INTERVIEWER_SYSTEM_PROMPT
    assert "提问状态（主问题、子问题、追问、澄清提问）下只能提问" in INTERVIEWER_SYSTEM_PROMPT
    assert "禁止表达个人观点、结论、建议" in INTERVIEWER_SYSTEM_PROMPT
    assert "所有提问类型都适用同一保真规则" in INTERVIEWER_SYSTEM_PROMPT
    assert "任何提问都只输出对[下一步内容]的完整表达" in INTERVIEWER_SYSTEM_PROMPT
    assert "末尾必须以问号（？或?）结束" in INTERVIEWER_SYSTEM_PROMPT
    assert "允许在提问前追加最多1句极短过渡语" in INTERVIEWER_SYSTEM_PROMPT
    assert "过渡语只能出现在问句前" in INTERVIEWER_SYSTEM_PROMPT
    assert "推荐短过渡白名单" in INTERVIEWER_SYSTEM_PROMPT
    assert "过渡强度判定" in INTERVIEWER_SYSTEM_PROMPT
    assert "none（首轮首问）" in INTERVIEWER_SYSTEM_PROMPT
    assert "soft（同场景）" in INTERVIEWER_SYSTEM_PROMPT
    assert "strong（跨场景）" in INTERVIEWER_SYSTEM_PROMPT
    assert "过渡语去重复" in INTERVIEWER_SYSTEM_PROMPT
    assert "禁止把具体题干泛化为“这个问题/这个话题”" in INTERVIEWER_SYSTEM_PROMPT
    assert "禁止省略任何前提条件、对象、阈值数字、比较关系、因果关系" in INTERVIEWER_SYSTEM_PROMPT
    assert "只做完整复述[下一步内容]" in INTERVIEWER_SYSTEM_PROMPT
    assert "禁止追加题干外追问，禁止替候选人回答" in INTERVIEWER_SYSTEM_PROMPT
    assert "“我觉得”“我认为”“我更偏向”" in INTERVIEWER_SYSTEM_PROMPT
    assert "“你可以”“你应该”“建议你”" in INTERVIEWER_SYSTEM_PROMPT
    assert "“其实就是”“本质上是”" in INTERVIEWER_SYSTEM_PROMPT
    assert "必须先重写为纯问句再输出" in INTERVIEWER_SYSTEM_PROMPT
    assert "建议不超过14字" in INTERVIEWER_SYSTEM_PROMPT
    assert "建议不超过20字" in INTERVIEWER_SYSTEM_PROMPT


def test_build_interview_context_ask_question_injects_fidelity_requirements():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    context = svc._build_interview_context(
        decision=Decision(
            next_action="next_question",
            next_prompt="",
            reason="semantic_enough_coverage",
            coverage_score=0.8,
        ),
        next_question_or_followup="“满500送100”活动后，总销售额增长30%，但客单价从580降到520，可能原因是什么？",
        flow_state=service.ASK_QUESTION,
    )

    assert "[题干保真要求]" in context
    assert "必须保留全部关键信息" in context
    assert "主体对象、场景背景、前提条件、动作要求" in context
    assert "若[下一步内容]含“背景说明+提问要求”，两部分必须都保留" in context
    assert "禁止只输出问题尾句" in context
    assert "若含对立观点前置背景（如“有人说…也有人说…”），该背景必须完整保留" in context
    assert "禁止省略、合并、替换任何关键数字或条件" in context


def test_build_interview_context_ask_question_requires_background_and_question_both_retained():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    prompt_with_background = (
        "互联网公司会按连续七天没登录做流失预警，线下店没有登录数据。"
        "你会用哪三个具体信号判断顾客可能要流失？"
    )
    context = svc._build_interview_context(
        decision=Decision(
            next_action="next_question",
            next_prompt="",
            reason="semantic_enough_coverage",
            coverage_score=0.8,
        ),
        next_question_or_followup=prompt_with_background,
        flow_state=service.ASK_QUESTION,
    )

    assert f"[下一步内容] {prompt_with_background}" in context
    assert "若[下一步内容]含“背景说明+提问要求”，两部分必须都保留" in context
    assert "禁止只输出问题尾句" in context


def test_build_interview_context_ask_clarify_injects_repeat_first_requirements():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    context = svc._build_interview_context(
        decision=Decision(
            next_action="clarify",
            next_prompt="题目是分析活动后的销售额和客单价变化。",
            reason="candidate_requested_clarification",
            coverage_score=0.0,
        ),
        next_question_or_followup="我再解释一下这道题。",
        flow_state=service.ASK_CLARIFY,
    )

    assert "[题意澄清要求]" in context
    assert "先复述题干关键信息，再做一句简短解释" in context
    assert "解释不得新增考察点" in context


def test_flow_state_origin_mapping():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )
    assert svc._flow_state_origin(service.ASK_QUESTION) == "bank_question"
    assert svc._flow_state_origin(service.ASK_FOLLOWUP) == "follow_up"
    assert svc._flow_state_origin(service.ASK_CLARIFY) == "clarify"
    assert svc._flow_state_origin(service.WRAP_UP) == "wrap_up"


def test_resolve_delivery_state_prefers_next_response_state_before():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )
    next_response = service.FlowResponse(
        state_before=service.ASK_CLARIFY,
        state_after=service.WAIT_ANSWER,
        interviewer_text="澄清文本",
        decision=None,
        question_id="q1",
        transition_trace=["ASK_CLARIFY -> WAIT_ANSWER"],
    )

    resolved = svc._resolve_delivery_state(service.WAIT_ANSWER, next_response)
    assert resolved == service.ASK_CLARIFY


def test_scene_transition_uses_delivery_state_for_cross_scene_next_question():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_mode=True,
        interview_questions=[
            {"question_id": "q1", "main_question": "Q1", "scenario": "场景A"},
            {"question_id": "q2", "main_question": "Q2", "scenario": "场景B"},
        ],
    )
    svc._build_question_context_segments(svc.interview_questions or [])

    decision = Decision(
        next_action="next_question",
        next_prompt="",
        reason="semantic_enough_coverage",
        coverage_score=0.9,
    )
    next_response = service.FlowResponse(
        state_before=service.ASK_QUESTION,
        state_after=service.WAIT_ANSWER,
        interviewer_text="Q2",
        decision=None,
        question_id="q2",
        transition_trace=["ASK_QUESTION -> WAIT_ANSWER"],
    )

    delivery_state = svc._resolve_delivery_state(service.WAIT_ANSWER, next_response)
    is_scene_transition = svc._compute_scene_transition_for_delivery(
        decision=decision,
        delivery_state=delivery_state,
        current_question_id="q1",
        next_question_id=next_response.question_id,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup=next_response.interviewer_text,
        flow_state=delivery_state,
        has_candidate_speech=True,
        is_scene_transition=is_scene_transition,
    )

    assert delivery_state == service.ASK_QUESTION
    assert is_scene_transition is True
    assert "[过渡强度] strong" in context
    assert "当前为strong过渡" in context


def test_scene_transition_uses_delivery_state_for_same_scene_next_question():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_mode=True,
        interview_questions=[
            {"question_id": "q1", "main_question": "Q1", "scenario": "场景A"},
            {"question_id": "q2", "main_question": "Q2", "scenario": "场景A"},
        ],
    )
    svc._build_question_context_segments(svc.interview_questions or [])

    decision = Decision(
        next_action="next_question",
        next_prompt="",
        reason="semantic_enough_coverage",
        coverage_score=0.9,
    )
    next_response = service.FlowResponse(
        state_before=service.ASK_QUESTION,
        state_after=service.WAIT_ANSWER,
        interviewer_text="Q2",
        decision=None,
        question_id="q2",
        transition_trace=["ASK_QUESTION -> WAIT_ANSWER"],
    )

    delivery_state = svc._resolve_delivery_state(service.WAIT_ANSWER, next_response)
    is_scene_transition = svc._compute_scene_transition_for_delivery(
        decision=decision,
        delivery_state=delivery_state,
        current_question_id="q1",
        next_question_id=next_response.question_id,
    )
    context = svc._build_interview_context(
        decision=decision,
        next_question_or_followup=next_response.interviewer_text,
        flow_state=delivery_state,
        has_candidate_speech=True,
        is_scene_transition=is_scene_transition,
    )

    assert delivery_state == service.ASK_QUESTION
    assert is_scene_transition is False
    assert "[过渡强度] soft" in context
    assert "当前为soft过渡" in context


def test_clarify_delivery_state_still_injects_repeat_first_requirements_after_flow_transition():
    class _AlwaysClarifyJudge:
        async def decide(
            self,
            question,
            candidate_answer,
            follow_up_count,
            clarify_count,
            evidence=None,
        ):
            return Decision(
                next_action="clarify",
                next_prompt=question,
                reason="candidate_requested_repeat",
                coverage_score=0.0,
            )

    async def _run():
        flow = InterviewFlow(
            questions=[
                {
                    "question_id": "q1",
                    "main_question": "请介绍一个你主导的项目。",
                    "max_clarifies": 1,
                }
            ],
            judge=_AlwaysClarifyJudge(),
        )
        await flow.produce_interviewer_message()  # INTRO -> ASK_QUESTION
        await flow.produce_interviewer_message()  # ASK_QUESTION -> WAIT_ANSWER
        answer_response = await flow.receive_candidate_answer("我没听清，可以再说一遍吗")
        assert answer_response.state_after == ASK_CLARIFY
        decision = answer_response.decision
        assert decision is not None

        next_response = await flow.produce_interviewer_message()
        assert next_response.state_before == ASK_CLARIFY
        assert flow.state == service.WAIT_ANSWER

        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
        )
        delivery_state = svc._resolve_delivery_state(flow.state, next_response)
        context = svc._build_interview_context(
            decision=decision,
            next_question_or_followup=next_response.interviewer_text,
            flow_state=delivery_state,
        )
        origin = svc._flow_state_origin(delivery_state)
        prompt_constrained = delivery_state in (service.ASK_QUESTION, service.ASK_CLARIFY)

        assert "[题意澄清要求]" in context
        assert origin == "clarify"
        assert prompt_constrained is True

    asyncio.run(_run())


def test_is_scene_transition_between_questions_uses_segment_mapping():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_mode=True,
        interview_questions=[
            {"question_id": "q1", "main_question": "Q1", "scenario": "项目复盘"},
            {"question_id": "q2", "main_question": "Q2", "scenario": "项目复盘"},
            {"question_id": "q3", "main_question": "Q3", "scenario": "故障处理"},
        ],
    )
    svc._build_question_context_segments(svc.interview_questions or [])

    assert svc._is_scene_transition_between_questions("q1", "q2") is False
    assert svc._is_scene_transition_between_questions("q2", "q3") is True
    assert svc._is_scene_transition_between_questions("q3", "q404") is False


def test_calc_segment_context_char_len_accumulates_followups_and_scene_subquestions():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_mode=True,
        interview_questions=[
            {"question_id": "q1", "main_question": "Q1", "scenario": "项目复盘"},
            {"question_id": "q2", "main_question": "Q2", "scenario": "项目复盘"},
            {"question_id": "q3", "main_question": "Q3", "scenario": "故障处理"},
        ],
    )
    svc._build_question_context_segments(svc.interview_questions or [])

    svc._activate_context_segment("q1")
    svc._append_history_message("user", "回答-主问题")
    base_len = svc._calc_segment_context_char_len("本轮上下文")
    assert base_len == len("回答-主问题") + len("本轮上下文")

    svc._append_history_message("assistant", "追问-请补充细节")
    followup_len = svc._calc_segment_context_char_len("本轮上下文")
    assert followup_len > base_len

    svc._activate_context_segment("q2")
    scene_len = svc._calc_segment_context_char_len("场景子问题上下文")
    assert scene_len >= followup_len

    svc._activate_context_segment("q3")
    isolated_len = svc._calc_segment_context_char_len("新场景上下文")
    assert isolated_len == len("新场景上下文")


def test_calc_segment_context_char_len_non_scene_questions_do_not_cross_contaminate():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_mode=True,
        interview_questions=[
            {"question_id": "q1", "main_question": "Q1", "scenario": ""},
            {"question_id": "q2", "main_question": "Q2", "scenario": ""},
        ],
    )
    svc._build_question_context_segments(svc.interview_questions or [])

    svc._activate_context_segment("q1")
    svc._append_history_message("user", "Q1-回答")
    q1_len = svc._calc_segment_context_char_len("Q1-context")
    assert q1_len == len("Q1-回答") + len("Q1-context")

    svc._activate_context_segment("q2")
    q2_len = svc._calc_segment_context_char_len("Q2-context")
    assert q2_len == len("Q2-context")


def test_load_interview_global_turn_limit_default(monkeypatch):
    monkeypatch.delenv("INTERVIEW_GLOBAL_TURN_LIMIT", raising=False)
    assert service._load_interview_global_turn_limit() == 300


def test_load_interview_global_turn_limit_invalid_fallback(monkeypatch):
    monkeypatch.setenv("INTERVIEW_GLOBAL_TURN_LIMIT", "abc")
    assert service._load_interview_global_turn_limit() == 300
    monkeypatch.setenv("INTERVIEW_GLOBAL_TURN_LIMIT", "0")
    assert service._load_interview_global_turn_limit() == 300
    monkeypatch.setenv("INTERVIEW_GLOBAL_TURN_LIMIT", "-1")
    assert service._load_interview_global_turn_limit() == 300


def test_voice_bot_service_init_uses_interview_global_turn_limit_env(monkeypatch):
    async def _run():
        monkeypatch.setenv("INTERVIEW_GLOBAL_TURN_LIMIT", "300")
        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
            interview_mode=True,
        )
        await svc.init()
        assert svc.interview_flow is not None
        assert svc.interview_flow.global_turn_limit == 300

    asyncio.run(_run())
