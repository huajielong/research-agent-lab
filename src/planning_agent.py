import json
import re
from typing import List
from datetime import datetime
from aisuite import Client
from src.agents import (
    DEFAULT_MODEL,
    research_agent,
    writer_agent,
    editor_agent,
)

client = Client()


def clean_json_block(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return raw.strip("` \n")


from typing import List
import json, ast


def planner_agent(topic: str, model: str = DEFAULT_MODEL) -> List[str]:
    prompt = f"""
You are a planning agent responsible for organizing a research workflow using multiple intelligent agents.

🧠 Available agents:
- Research agent: MUST begin with a broad **web search using Tavily** to identify only **relevant** and **authoritative** items (e.g., high-impact venues, seminal works, surveys, or recent comprehensive sources). The output of this step MUST capture for each candidate: title, authors, year, venue/source, URL, and (if available) DOI.
- Research agent: AFTER the Tavily step, perform a **targeted arXiv search** ONLY for the candidates discovered in the web step (match by title/author/DOI). If an arXiv preprint/version exists, record its arXiv URL and version info. Do NOT run a generic arXiv search detached from the Tavily results.
- Writer agent: drafts based on research findings.
- Editor agent: reviews, reflects on, and improves drafts.

🎯 Produce a clear step-by-step research plan **as a valid Python list of strings** (no markdown, no explanations). 
Each step must be atomic, actionable, and assigned to one of the agents.
Maximum of 7 steps.

🚫 DO NOT include steps like “create CSV”, “set up repo”, “install packages”.
✅ Focus on meaningful research tasks (search, extract, rank, draft, revise).
✅ The FIRST step MUST be exactly: 
"Research agent: Use Tavily to perform a broad web search and collect top relevant items (title, authors, year, venue/source, URL, DOI if available)."
✅ The SECOND step MUST be exactly:
"Research agent: For each collected item, search on arXiv to find matching preprints/versions and record arXiv URLs (if they exist)."

🔚 The FINAL step MUST instruct the writer agent to generate a comprehensive Markdown report that:
- Uses all findings and outputs from previous steps
- Includes inline citations (e.g., [1], (Wikipedia/arXiv))
- Includes a References section with clickable links for all citations
- Preserves earlier sources
- Is detailed and self-contained

Topic: "{topic}"
"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=1,
    )

    raw = response.choices[0].message.content.strip()

    # --- 稳健解析：JSON -> ast -> 兜底 ---
    def _coerce_to_list(s: str) -> List[str]:
        # 先尝试严格 JSON
        try:
            obj = json.loads(s)
            if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
                return obj[:7]
        except json.JSONDecodeError:
            pass
        # 再尝试 Python 字面量列表
        try:
            obj = ast.literal_eval(s)
            if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
                return obj[:7]
        except Exception:
            pass
        # 若存在代码围栏则尝试提取其中内容
        if s.startswith("```") and s.endswith("```"):
            inner = s.strip("`")
            try:
                obj = ast.literal_eval(inner)
                if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
                    return obj[:7]
            except Exception:
                pass
        return []

    steps = _coerce_to_list(raw)

    # 强制顺序与最小契约（required contract）
    required_first = "Research agent: Use Tavily to perform a broad web search and collect top relevant items (title, authors, year, venue/source, URL, DOI if available)."
    required_second = "Research agent: For each collected item, search on arXiv to find matching preprints/versions and record arXiv URLs (if they exist)."
    final_required = "Writer agent: Generate the final comprehensive Markdown report with inline citations and a complete References section with clickable links."

    def _ensure_contract(steps_list: List[str]) -> List[str]:
        if not steps_list:
            return [
                required_first,
                required_second,
                "Research agent: Synthesize and rank findings by relevance, recency, and authority; deduplicate by title/DOI.",
                "Writer agent: Draft a structured outline based on the ranked evidence.",
                "Editor agent: Review for coherence, coverage, and citation completeness; request fixes.",
                final_required,
            ]
        # 若前两步缺失或顺序不对，则注入/替换
        steps_list = [s for s in steps_list if isinstance(s, str)]
        if not steps_list or steps_list[0] != required_first:
            steps_list = [required_first] + steps_list
        if len(steps_list) < 2 or steps_list[1] != required_second:
            # 移除与 Tavily 结果无关的通用 arXiv 步骤
            steps_list = (
                [steps_list[0]]
                + [required_second]
                + [
                    s
                    for s in steps_list[1:]
                    if "arXiv" not in s or "For each collected item" in s
                ]
            )
        # 确保「最终报告」始终是最后一步（main.py 会把最后一步的输出当报告）
        steps_list = [s for s in steps_list if s != final_required]
        if len(steps_list) >= 7:
            steps_list = steps_list[:6]
        steps_list.append(final_required)
        # 最多保留 7 步
        return steps_list[:7]

    steps = _ensure_contract(steps)

    return steps


def executor_agent_step(step_title: str, history: list, prompt: str):
    """
    执行执行器智能体的一个步骤。
    返回：
        - step_title (str)
        - agent_name (str)
        - output (str)
    """

    # 构建结构化的增强上下文
    context = f"📘 User Prompt:\n{prompt}\n\n📜 History so far:\n"
    for i, (desc, agent, output) in enumerate(history):
        if "draft" in desc.lower() or agent == "writer_agent":
            context += f"\n✍️ Draft (Step {i + 1}):\n{output.strip()}\n"
        elif "feedback" in desc.lower() or agent == "editor_agent":
            context += f"\n🧠 Feedback (Step {i + 1}):\n{output.strip()}\n"
        elif "research" in desc.lower() or agent == "research_agent":
            context += f"\n🔍 Research (Step {i + 1}):\n{output.strip()}\n"
        else:
            context += f"\n🧩 Other (Step {i + 1}) by {agent}:\n{output.strip()}\n"

    enriched_task = f"""{context}

🧩 Your next task:
{step_title}
"""

    # 根据步骤内容选择对应的智能体
    step_lower = step_title.lower()
    if "research" in step_lower:
        content, _ = research_agent(prompt=enriched_task)
        print("🔍 研究智能体输出：", content)
        return step_title, "research_agent", content
    elif "draft" in step_lower or "write" in step_lower:
        content, _ = writer_agent(prompt=enriched_task)
        return step_title, "writer_agent", content
    elif "revise" in step_lower or "edit" in step_lower or "feedback" in step_lower:
        content, _ = editor_agent(prompt=enriched_task)
        return step_title, "editor_agent", content
    else:
        raise ValueError(f"Unknown step type: {step_title}")
