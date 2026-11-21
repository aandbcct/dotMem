# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Generate faithful explicit candidates; Jev decides persistence and relations.
"""
import logging

from memhub.agent import prompts
from memhub.providers.llm import LLMClient, merge_extract_results, truncate_middle
from memhub.sdk_models import ChatMessage

logger = logging.getLogger("memhub.agent.extract")

class Extractor:
    """Extracts identity/fact/intention memories from one LLM JSON call."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        max_content_chars: int = 0,
        few_shot_enabled: bool = False,
    ):
        self.llm = llm
        self.max_content_chars = max_content_chars
        self.few_shot_enabled = few_shot_enabled

    async def extract(
        self,
        *,
        content: str,
        current_time: str,
        app_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        current_messages: list[ChatMessage] | None = None,
        history_messages: list[ChatMessage] | None = None,
    ) -> dict:
        """Return extracted fields; ``basic_info`` is persisted later by MemAgent."""
        tmpl = prompts.pick(prompts.EXTRACT_ZH, prompts.EXTRACT_EN, content)
        if self.few_shot_enabled:
            tmpl += prompts.pick(
                prompts.EXTRACT_FEW_SHOT_ZH,
                prompts.EXTRACT_FEW_SHOT_EN,
                content,
            )

        def _build_system(chunk: str) -> str:
            return tmpl.format(
                content=chunk,
                current_time=current_time,
            )

        llm_content = self._prepare_content(
            _message_native_content(current_messages, history_messages)
            if current_messages is not None
            else content
        )
        parsed = await self.llm.chat_json_for_content(
            content=llm_content,
            build_system=_build_system,
            merge_results=merge_extract_results,
        )
        if not isinstance(parsed, dict) or not parsed:
            logger.warning(
                "extract: empty/unparseable LLM output for content len=%d (preview=%r)",
                len(content),
                content[:120],
            )
            parsed = {} if not isinstance(parsed, dict) else parsed

        identity = parsed.get("identity") or []
        facts = parsed.get("facts") or []
        intentions = parsed.get("intentions") or []
        is_ephemeral = bool(parsed.get("is_ephemeral", False))
        basic_info = parsed.get("basic_info")
        if not isinstance(basic_info, dict):
            basic_info = {}

        logger.debug(
            "extract identity=%d facts=%d intentions=%d ephemeral=%s basic_info=%s",
            len(identity) if isinstance(identity, list) else 0,
            len(facts) if isinstance(facts, list) else 0,
            len(intentions) if isinstance(intentions, list) else 0,
            is_ephemeral,
            bool(basic_info),
        )

        out: dict = {
            "identity": identity if isinstance(identity, list) else [],
            "facts": facts if isinstance(facts, list) else [],
            "intentions": intentions if isinstance(intentions, list) else [],
            "is_ephemeral": is_ephemeral,
            "basic_info": basic_info,
        }
        return out

    async def extract_system1(
        self,
        *,
        content: str,
        current_time: str,
        current_messages: list[ChatMessage] | None = None,
        feedback: str = "",
    ) -> dict:
        """Extract System1 memories with a stable contract and separate user input.

        ``feedback`` is the decision provider's rejection of a previous extraction of
        the same source; it is appended verbatim so the regeneration can address it.
        """
        llm_content = self._prepare_content(
            _message_native_content(current_messages, None)
            if current_messages is not None
            else content
        )
        system = (
            "Generate faithful explicit atomic candidates from the supplied conversation. "
            "Choose the output language before extracting: read only the prose in the current [user] messages, "
            "ignoring quoted titles, code, and all assistant text. If the user's prose is in English, output in English. "
            "If it is in Chinese, output in Simplified Chinese (简体中文), even if the user uses traditional characters. "
            "Use this language for all candidate prose, including facts supplied by the assistant. "
            "Keep exact commands, paths, technical identifiers, model names, titles and quoted errors unchanged. "
            "Extract durable user- and task-specific records, not every sentence; "
            "the full conversation is retained separately as source evidence. Jev decides knownness and update relations. "
            "Keep supported, reusable personal and project facts and explicit plans. Retain names, numbers and units, "
            "conditions, exceptions, negation, and reasons needed to understand or apply each record. "
            "Text the user brings for a task (a draft email, a passage to polish, a message to translate, a post) is "
            "first-person evidence when it is written by or about the user: extract the user's own activities, habits, "
            "possessions, health conditions, relationships, places and experiences it states, as facts about the user "
            "with the task context (e.g. 'In a passage the user wrote about their mornings, the user swims laps at the "
            "university pool before training'). Text written by someone else (an email the user received, a quoted "
            "colleague) is evidence about that person; attribute it to them, never to the user. "
            "Separate distinct events from current state and keep one dimension per candidate. "
            "Make every content self-contained: name its subject and objects and resolve pronouns from this conversation. "
            "Write one concise sentence per candidate where practical. Compress framing, never evidence: "
            "keep a fact's conditions and reasons with it. Consolidate restatements within this conversation, "
            "but never merge claims with different values, times, attribution or scope. "
            "Preserve the user's explicit positive/negative evaluation of an experience and the stated feature causing it, "
            "with its context. Do not reduce 'I found X engaging because Y' to only 'the user did X'; "
            "also do not generalize that evaluation to unrelated situations or an enduring trait. "
            "A generic question or one-off formatting request is not a durable record or a user plan. "
            "A question with a specific personal or project context may be an event; keep that concrete context "
            "without inventing a preference, goal or answer. "
            "Keep concrete assistant-provided details tied to the user's task (names, addresses, phone numbers, amounts, "
            "commands, technical rationale, recommendations), but omit generic explanations, pleasantries, repetitive "
            "procedural advice, and transient operational action or progress narration (such as 'the assistant added/edited/"
            "updated/ran/confirmed...'). "
            "A candidate must be reusable in a later session. Omit incidental run artifacts and transient progress, "
            "but keep stable project paths, actionable contact details, and meaningful negatives that establish a "
            "constraint, decision, cancellation or current state. "
            "State the detail directly, not as a transcript of who said what. Preserve attribution where dropping it "
            "would make a recommendation, estimate, or unverified claim sound like an established fact. "
            "Record what the user states or does; do not generalize it into a broader preference or trait. "
            "When the user asks the assistant to forget, stop using or not remember something about them, that is a "
            "withdrawal, not a new fact: do NOT extract the withdrawn detail (from this request or from earlier "
            "messages of the same conversation) as a user fact, preference or profile item, and add ONE fact "
            "stating only that the user withdrew it and the assistant must not use it, e.g. 'The user withdrew "
            "consent for the assistant to remember or use that the user plays chess; do not personalize with it.' "
            "Never turn assistant advice into a user possession or preference. "
            "When a plan uses an already-owned item ('my device'), retain that possession separately; ownership is not a future plan. "
            "Set speaker to the actual source; never present assistant-provided information as user-confirmed. "
            "Negate only what was explicitly denied: 'that preference is my colleague's, not mine' identifies "
            "whose preference it is; it does not mean the user dislikes the thing. "
            "A return to a former state still requires a candidate. "
            "Current time is only a reference for explicitly stated relative dates; do not insert an as-of "
            "time or validity boundary when the source supplies none. Do not omit content because it might be known. "
            "For an explicitly limited EXCEPTION or validity window, add valid_from/valid_until "
            "(YYYY-MM-DD) and temporal_scope (verbatim time phrase). Omit all three keys otherwise; never fill defaults. "
            "If a standing rule and a dated exception are both stated, emit two records: the normal value with no "
            "validity fields, and the temporary value with its explicit dates in valid_from/valid_until and temporal_scope. "
            "A reason/condition is NOT a temporal_scope. A plan's scheduled event date is NOT its validity window; "
            "keep the event date in content, without valid_from/valid_until. 'Already own' and 'currently' do not supply validity bounds. "
            'Return {"identity":[{"content":"current user state", "speaker":"user", "durable":true}], '
            '"facts":[{"content":"event or concrete detail", "speaker":"user|assistant", "durable":true}], '
            '"intentions":[{"content":"explicit user plan", "speaker":"user", "durable":true}], "basic_info":{}}. '
            'Set durable=false only for one-off operational narration, transient progress, pleasantries, '
            'or incidental run artifacts with no continuing value. A reusable fact may come from either speaker. '
            'durable=false candidates are dropped before adjudication. Use true or omit durable when unsure. '
            "Never mark durable=false on a candidate carrying a name, number, unit, path, command, version, "
            "identifier, quoted error or explicit condition; concrete evidence stays reusable whichever speaker supplied it. "
            "identity holds explicitly stated, stable attributes OF THE USER: role, residence, relationships, recurring habits, "
            "possessions and standing personal preferences. One attribute per item. A fact about a colleague is never an "
            "identity item, even if the user states it; do not infer personality or preference from one-off requests, "
            "assistant suggestions or project configuration. "
            "facts holds events, project facts and rules, and useful concrete information supplied by the assistant. "
            "intentions holds only what the user explicitly intends to do or achieve, not what they ask the assistant to do. "
            "A future plan goes ONLY in intentions, "
            "not also in facts. A completed booking is a fact, distinct from the scheduled future activity. Put each candidate in ONE list; "
            "do not repeat it elsewhere. In particular, a colleague's hobby belongs only in facts; stating it is not the user's "
            "hobby does not create a second record. Empty lists are valid. Always keep basic_info empty. "
            "Do not write placeholder text or explanations. "
            "Language examples (not evidence): user '我在維護 Kestrel' + assistant 'The config is at conf/app.yml' "
            '→ {"content":"助手指出 Kestrel 的配置位于 conf/app.yml。","speaker":"assistant"}; '
            "user 'Our handbook is titled 《團隊手冊》.' "
            '→ {"content":"The team handbook is titled 《團隊手冊》.","speaker":"user"}. '
            "Check that generated prose follows the current user's language. Use Simplified Chinese only for Chinese prose."
        )
        if feedback:
            system += f"\nA previous extraction of this source was rejected: {feedback}"
        user = f"Current time: {current_time or '(unknown)'}\n\nConversation:\n{llm_content}"
        parsed = await self.llm.chat_json_for_content(
            content=llm_content,
            build_system=lambda chunk: system
            + f"\nCurrent time: {current_time or '(unknown)'}",
            merge_results=merge_extract_results,
            user=user,
            temperature=0.0,
            max_tokens=3072,
        )
        if not isinstance(parsed, dict):
            parsed = {}
        return {
            "identity": (
                parsed.get("identity")
                if isinstance(parsed.get("identity"), list)
                else []
            ),
            "facts": (
                parsed.get("facts") if isinstance(parsed.get("facts"), list) else []
            ),
            "intentions": (
                parsed.get("intentions")
                if isinstance(parsed.get("intentions"), list)
                else []
            ),
            "is_ephemeral": bool(parsed.get("is_ephemeral", False)),
            "basic_info": (
                parsed.get("basic_info")
                if isinstance(parsed.get("basic_info"), dict)
                else {}
            ),
        }

    def _prepare_content(self, content: str) -> str:
        if self.max_content_chars <= 0 or len(content) <= self.max_content_chars:
            return content
        logger.info(
            "extract: truncating content %d -> %d chars",
            len(content),
            self.max_content_chars,
        )
        return truncate_middle(content, self.max_content_chars)

def _message_native_content(
    current_messages: list[ChatMessage],
    history_messages: list[ChatMessage] | None,
) -> str:
    def _format(messages: list[ChatMessage]) -> str:
        return "\n".join(
            f"[{message.role}]: {message.content} (message_id={index})"
            for index, message in enumerate(messages)
        )

    history = _format(history_messages or []) or "(none)"
    return (
        "Recent context (for reference resolution only; never extract new facts from it):\n"
        f"{history}\n\n"
        "Current messages (extract durable memories only from these messages):\n"
        f"{_format(current_messages)}"
    )
