"""Live user messages and the Harness AskUser answerer."""
import asyncio
import json
from collections.abc import Awaitable, Callable

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai_harness.ask_user import AskUserAnswer, AskUserRequest, AskUserResponse, Question, QuestionOption, check_response


class Interaction(AbstractCapability):
    def __init__(self) -> None:
        self.active = False
        self.context: RunContext | None = None
        self.pending: list[str] = []
        self.on_question: Callable[[str], Awaitable[None]] | None = None
        self.waiting: tuple[Question, asyncio.Future[AskUserAnswer]] | None = None
        self.lock = asyncio.Lock()

    def open(self, on_question) -> None:
        if self.active:
            raise RuntimeError('会话已有运行中的请求')
        self.active = True
        self.on_question = on_question

    async def wrap_run(self, ctx, *, handler):
        try:
            return await handler()
        finally:
            self.close()

    async def before_run(self, ctx: RunContext) -> None:
        self.context = ctx
        for text in self.pending:
            ctx.enqueue(text)
        self.pending.clear()

    async def before_tool_validate(self, ctx, *, call, tool_def, args):
        if call.tool_name != 'ask_user_question':
            return args
        try:
            data = json.loads(args) if isinstance(args, str) else args
        except json.JSONDecodeError:
            return args
        if not isinstance(data, dict) or not isinstance(data.get('questions'), list):
            return args
        questions = []
        for question in data['questions']:
            if isinstance(question, dict) and 'multiSelect' in question:
                question = dict(question)
                alias = question.pop('multiSelect')
                if 'multi_select' in question and question['multi_select'] != alias:
                    raise ModelRetry('multiSelect 与 multi_select 冲突，请只使用 multi_select。')
                question.setdefault('multi_select', alias)
            questions.append(question)
        return {**data, 'questions': questions}

    def feedback(self, text: str) -> bool:
        if not self.active:
            return False
        if self.waiting is not None:
            question, future = self.waiting
            if future.done():
                self.waiting = None
                return False
            labels = [option.label for option in question.options]
            choice = text.strip()
            selected = labels[int(choice) - 1] if choice in map(str, range(1, len(labels) + 1)) else choice
            answer = AskUserAnswer(header=question.header, selected=(selected,)) if selected in labels else AskUserAnswer(
                header=question.header, custom_answer=text,
            )
            check_response(AskUserRequest(questions=(question,)), AskUserResponse(answers=(answer,)))
            future.set_result(answer)
            self.waiting = None
        elif self.context is not None:
            self.context.enqueue(text)
        else:
            self.pending.append(text)
        return True

    async def ask(self, request: AskUserRequest) -> AskUserResponse:
        async with self.lock:
            return await self._ask(request)

    async def _ask(self, request: AskUserRequest, prefix: str = "") -> AskUserResponse:
        if self.on_question is None:
            raise RuntimeError('当前外壳未提供用户提问回调')
        answers = []
        for question in request.questions:
            future = asyncio.get_running_loop().create_future()
            self.waiting = (question, future)
            options = '\n'.join(f'{i}. {option.label}' + (f'：{option.description}' if option.description else '')
                                for i, option in enumerate(question.options, 1))
            try:
                await self.on_question(f'{prefix}\n\n**{question.header}**\n\n{question.question}\n\n{options}\n\n输入选项编号、名称或直接回答。')
                answers.append(await future)
            finally:
                self.waiting = None
        return AskUserResponse(answers=tuple(answers))

    async def approve(
        self, details: str, *, rememberable: bool, header: str = "工具审批",
        prompt: str = "允许此次调用？",
        remember_label: str = "本会话允许相同调用",
    ) -> tuple[bool, bool]:
        if self.on_question is None:
            return False, False
        labels = ["本次允许", "拒绝"]
        if rememberable:
            labels.append(remember_label)
        question = Question(header=header, question=prompt,
                            options=tuple(QuestionOption(label=label) for label in labels))
        try:
            async with asyncio.timeout(300), self.lock:
                response = await self._ask(AskUserRequest(questions=(question,)), prefix=details)
        except TimeoutError:
            return False, False
        if response.cancelled or not response.answers:
            return False, False
        selected = response.answers[0].selected
        remember = rememberable and selected == (remember_label,)
        return selected == (labels[0],) or remember, remember

    def close(self) -> None:
        self.active = False
        self.context = None
        self.pending.clear()
        self.on_question = None
