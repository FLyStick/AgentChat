import inspect
from typing import List
from typing import Any, Awaitable, Callable, Optional, Sequence, Type, TypeVar, Union
from langgraph.types import Command
from langchain_core.messages import BaseMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langchain_core.tools import tool as create_tool
from langgraph.graph import END, START, MessagesState, StateGraph

from agentchat.core.models.manager import ModelManager
from agentchat.services.sandbox import PyodideSandbox
from agentchat.utils.extract import extract_and_combine_codeblocks

EvalFunction = Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]]
EvalCoroutine = Callable[[str, dict[str, Any]], Awaitable[tuple[str, dict[str, Any]]]]

class CodeActState(MessagesState):
    """CodeAct Agent 的状态：在消息基础上增加待执行的脚本与执行上下文。"""

    script: Optional[str]
    """待执行的 Python 代码脚本。"""
    context: dict[str, Any]
    """执行上下文：包含可用工具与变量。"""


StateSchema = TypeVar("StateSchema", bound=CodeActState)
StateSchemaType = Type[StateSchema]


def create_default_prompt(tools: list[StructuredTool], base_prompt: Optional[str] = None):
    """为 CodeAct Agent 创建默认提示词：列出可用工具的函数签名。"""
    tools = [t if isinstance(t, StructuredTool) else create_tool(t) for t in tools]
    prompt = f"{base_prompt}\n\n" if base_prompt else ""
    prompt += """You will be given a task to perform. You should output either
- a Python code snippet that provides the solution to the task, or a step towards the solution. Any output you want to extract from the code should be printed to the console. Code should be output in a fenced code block.
- text to be shown directly to the user, if you want to ask for more information or provide the final answer.

In addition to the Python Standard Library, you can use the following functions:
"""

    for tool in tools:
        prompt += f'''
def {tool.name}{str(inspect.signature(tool.func))}:
    """{tool.description}"""
    ...
'''

    prompt += """

Variables defined at the top level of previous code snippets can be referenced in your code.

Reminder: use Python code snippets to call tools"""
    return prompt


class CodeActAgent:
    """CodeAct Agent：模型生成代码，在沙箱中执行并反馈结果，循环直至完成任务。"""

    def __init__(self, tools, user_id):
        self.tools = tools
        self.user_id = user_id
        self.coder_model = ModelManager.get_conversation_model()

        self.setup_codeact_agent()


    def setup_codeact_agent(self):
        """初始化 CodeAct Agent：创建沙箱、求值函数与 Agent 图。"""
        sandbox = PyodideSandbox(allow_net=True)
        eval_fn = self.create_pyodide_eval_fn(sandbox)
        self.codeact_agent = self.create_codeact_agent(self.coder_model, self.tools, eval_fn)


    async def astream(self, messages: List[BaseMessage]):
        """流式调用：输出模型文本增量与完整状态快照。"""

        async for typ, chunk in self.codeact_agent.astream(
                {"messages": messages},
                stream_mode=["values", "messages"],
        ):
            if typ == "messages":
                # 模型文本增量
                yield chunk[0].content
            elif typ == "values":
                # 完整状态快照
                yield chunk

    def create_pyodide_eval_fn(self, sandbox: PyodideSandbox) -> EvalCoroutine:
        """创建基于 PyodideSandbox 的求值函数：执行代码并返回输出与新变量。
        """

        async def async_eval_fn(
                code: str, _locals: dict[str, Any]
        ) -> tuple[str, dict[str, Any]]:
            # 构造包装函数：执行传入代码并返回局部变量（出错时返回 error）
            wrapper_code = f"""
def execute():
    try:
        # Execute the provided code
{"\n".join(" " * 8 + line for line in code.strip().split("\n"))}
        return locals()
    except Exception as e:
        return {{"error": str(e)}}

execute()
    """
            # 将 _locals 中的函数转为源码、其他值转为字面量，拼成上下文初始化代码
            context_setup = ""
            for key, value in _locals.items():
                if callable(value):
                    # 获取函数源码
                    src = inspect.getsource(value)
                    context_setup += f"\n{src}"
                else:
                    context_setup += f"\n{key} = {repr(value)}"

            try:
                # 在沙箱中执行上下文初始化 + 用户代码
                response = await sandbox.execute(
                    code=context_setup + "\n\n" + wrapper_code,
                )
                # 检查执行是否成功（stderr 有输出视为失败）
                if response.stderr:
                    return f"Error during execution: {response.stderr}", {}

                # 获取 stdout 输出
                output = (
                    response.stdout
                    if response.stdout
                    else "<Code ran, no output printed to stdout>"
                )
                result = response.result

                # 若执行函数内部出错，返回错误信息
                if isinstance(result, dict) and "error" in result:
                    return f"Error during execution: {result['error']}", {}

                # 对比原始 locals，提取新增变量（排除下划线开头的内部变量）
                new_vars = {
                    k: v
                    for k, v in result.items()
                    if k not in _locals and not k.startswith("_")
                }
                return output, new_vars

            except Exception as e:
                return f"Error during PyodideSandbox execution: {repr(e)}", {}

        return async_eval_fn


    def create_codeact_agent(
        self,
        model: BaseChatModel,
        tools: Sequence[Union[StructuredTool, Callable]],
        eval_fn: Union[EvalFunction, EvalCoroutine],
        *,
        prompt: Optional[str] = None,
        state_schema: StateSchemaType = CodeActState,
    ) -> StateGraph:
        """创建 CodeAct Agent 图：模型生成代码 -> 沙箱执行 -> 反馈结果，循环直至结束。

        Args:
            model: 用于生成代码的语言模型
            tools: Agent 可用的工具列表，可以是 Python 函数或 StructuredTool 实例
            eval_fn: 在沙箱中执行代码的函数或协程。接收代码字符串和 locals 字典，
                返回 (stdout 输出, 新变量字典) 元组
            prompt: 可选的自定义系统提示词。为 None 时使用默认提示词，
                可通过 `create_default_prompt` 辅助函数定制：
                `create_default_prompt(tools, "You are a helpful assistant.")`
            state_schema: Agent 使用的状态模式

        Returns:
            实现 CodeAct 架构的 StateGraph
        """
        tools = [t if isinstance(t, StructuredTool) else create_tool(t) for t in tools]

        if prompt is None:
            prompt = create_default_prompt(tools)

        # 将工具暴露给代码沙箱（以函数形式注入上下文）
        tools_context = {tool.name: tool.func for tool in tools}

        def call_model(state: StateSchema) -> Command:
            """模型节点：生成回复，若含代码块则跳转沙箱执行，否则结束。"""
            messages = [{"role": "system", "content": prompt}] + state["messages"]
            response = model.invoke(messages)
            # 提取并合并所有代码块
            code = extract_and_combine_codeblocks(response.content)
            if code:
                return Command(goto="sandbox", update={"messages": [response], "script": code})
            else:
                # 无代码块：结束循环，直接回复用户
                return Command(update={"messages": [response], "script": None})

        # 若 eval_fn 是协程，则定义异步沙箱节点
        if inspect.iscoroutinefunction(eval_fn):

            async def sandbox(state: StateSchema):
                """异步沙箱节点：执行脚本，将输出作为用户消息反馈给模型。"""
                existing_context = state.get("context", {})
                context = {**existing_context, **tools_context}
                # 在沙箱中执行脚本
                output, new_vars = await eval_fn(state["script"], context)
                new_context = {**existing_context, **new_vars}
                return {
                    "messages": [{"role": "user", "content": output}],
                    "context": new_context,
                }
        else:

            def sandbox(state: StateSchema):
                """同步沙箱节点：执行脚本，将输出作为用户消息反馈给模型。"""
                existing_context = state.get("context", {})
                context = {**existing_context, **tools_context}
                # 在沙箱中执行脚本
                output, new_vars = eval_fn(state["script"], context)
                new_context = {**existing_context, **new_vars}
                return {
                    "messages": [{"role": "user", "content": output}],
                    "context": new_context,
                }

        # 构建图：START -> call_model -> (END | sandbox) -> call_model
        agent = StateGraph(state_schema)
        agent.add_node(call_model, destinations=(END, "sandbox"))
        agent.add_node(sandbox)
        agent.add_edge(START, "call_model")
        agent.add_edge("sandbox", "call_model")
        return agent.compile()
