"""Deterministic, offline brain.

Lets the whole agent run (tests, demo, CI) without a model API key, and serves as a
fallback when the model is unavailable. Precision is deliberately conservative:
anything ambiguous comes back as UNCLEAR / no answer, which routes to a human.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from autopilot.brain.base import (
    NOTE_SLOT_TAKEN,
    AnswerClaim,
    ComposePlan,
    ComposeResult,
    ConversationContext,
    DistilledFact,
    ReplyAnalysis,
    SchedulingIntent,
)
from autopilot.brain.text import detect_timezone, strip_quoted
from autopilot.insights import OBJECTION_LABELS, assess_at_risk, detect_objections, sentiment_trend
from autopilot.knowledge import topics_of
from autopilot.voice import DEFAULT_STYLE
from autopilot.models import Interest


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


UNSUBSCRIBE = _rx(r"unsubscribe|remove me|stop (emailing|contacting|messaging)|take me off|"
                  r"do not (contact|email)|don'?t (contact|email) me")
OFFER_ACCEPT = _rx(r"(accept|accepted|accepting|sign|signed|signing) (the|your) offer|"
                   r"(thrilled|happy|excited) to accept|offer letter (is )?signed")
OFFER_DECLINE = _rx(r"(decline|declining|turn down|turning down) (the|your) offer")
NOT_NOW = _rx(r"not (right )?now|not at the moment|maybe (later|in the future|down the road)|"
              r"reach (back )?out (again )?(in|after|next|around)|circle back|check back|"
              r"ping me (in|after|next)|in a few months|next (quarter|year)|after the holidays|"
              r"not a good time|timing isn'?t right|bad timing|just started (a )?(new )?(job|role)")
NOT_INTERESTED = _rx(r"not interested|no,? thanks|no thank you|not looking|happy (where i am|in my current|"
                     r"at my current)|i'?ll pass|going to pass|not for me|(isn'?t|not) a (good )?fit|"
                     r"not open to|staying (at|with|put)|accepted (another|a different) (offer|role|position)")
INTERESTED = _rx(r"interested|sounds (great|good|interesting|exciting)|(love|like) to (chat|talk|learn|hear)|"
                 r"happy to (chat|talk|connect|learn|hop)|open to|tell me more|keen|let'?s (chat|talk|connect)|"
                 r"count me in|\byes\b|\bsure\b|definitely|i'?m in\b|excited")
NEEDS_HUMAN = _rx(r"(speak|talk) (to|with) (a )?(real |actual )?(person|human)|are you (a )?(bot|ai|robot)|"
                  r"(talking|speaking) to (a )?(bot|ai|robot|real person|human|someone real)|bot or a real|"
                  r"lawyer|attorney|non-?compete|discriminat|harass|complain|counter ?offer|negotiat|"
                  r"competing offer|another offer|match (it|that|my)")
RESCHEDULE = _rx(r"reschedul|move (our|the) (call|meeting|interview|chat)|push (it|our|the) (back|call|meeting)|"
                 r"something came up|need to change (the )?time")
CANCEL = _rx(r"\bcancel")
LINK = _rx(r"(send|share) (me )?(a |your )?(calendar |booking |scheduling )?link|calendly|booking page")
DECLINE_SLOTS = _rx(r"none of (those|these|them)|(those|these) (don'?t|do not|won'?t) work|can'?t make (any|those)")
ANY_SLOT = _rx(r"any of (those|these|them)( times| slots| options)? (work|are fine|is fine|is good|work for me)|"
               r"either (works|is fine)|whichever|all (work|good|fine)|any (of them |of those )?work")
NEEDS_VISA = _rx(r"(need|require|would need|will need)[^.?!]{0,30}(sponsor|visa)")
NEEDS_REMOTE = _rx(r"(only|need|require|must)[^.?!]{0,30}(fully )?remote")
NEGATIVE = _rx(r"\b(no|not|cannot|can'?t|unable|don'?t|do not|isn'?t)\b")

_ORDINALS = [
    (_rx(r"\b(first|1st|option (1|one)|#1|number one|the earliest|soonest)\b"), 0),
    (_rx(r"\b(second|2nd|option (2|two)|#2|number two|middle one)\b"), 1),
    (_rx(r"\b(third|3rd|option (3|three)|#3|number three)\b"), 2),
]
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY_RX = _rx(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(day|nesday|rsday|urday)?\b|\btomorrow\b")
_TIME_RX = _rx(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b")
_LEAD_IN = _rx(r"^((also|and|plus|oh|so|but|lastly|finally|one more thing|quick question)[,:]?\s+)+")
_INFO_REQUEST = _rx(r"\b(tell me (more|about)|let me know (about|more)|would (love|like) to (know|hear|learn)|"
                    r"curious (about|to hear|to know)|fill me in|share more|more (details|info|information))\b")
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]

TOPIC_LABELS = {
    "compensation": "compensation", "benefits": "benefits", "location": "location and work setup",
    "process": "the interview process", "team": "the team", "visa": "visa sponsorship",
    "relocation": "relocation support", "health": "health benefits", "retirement": "the retirement plan",
    "pto": "time off and leave",
    "company": "the company", "role": "the role", "travel": "travel", "start_date": "start date",
}


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n+", text) if s.strip()]


def question_label(question: str) -> str:
    topics = sorted(topics_of(question))
    return TOPIC_LABELS.get(topics[0], topics[0]) if topics else question.rstrip("?").strip()


class HeuristicBrain:
    def analyze(self, ctx: ConversationContext) -> ReplyAnalysis:
        assert ctx.latest is not None
        text = strip_quoted(ctx.latest.body)
        a = ReplyAnalysis()
        a.questions = self._questions(text)
        a.candidate_timezone = detect_timezone(text)
        a.tone = self._tone(text)

        if UNSUBSCRIBE.search(text):
            a.unsubscribe, a.interest = True, Interest.NOT_INTERESTED
        elif OFFER_ACCEPT.search(text):
            a.offer_status, a.interest = "accepted", Interest.INTERESTED
        elif OFFER_DECLINE.search(text):
            a.offer_status, a.interest = "declined", Interest.NOT_INTERESTED
        elif NOT_NOW.search(text):
            a.interest = Interest.NOT_NOW
            a.follow_up_on = self._follow_up_date(text, ctx.now)
        elif NOT_INTERESTED.search(text):
            a.interest = Interest.NOT_INTERESTED
            a.not_interested_reason = self._reason(text)
        elif INTERESTED.search(text):
            a.interest = Interest.INTERESTED

        if m := NEEDS_HUMAN.search(text):
            a.needs_human, a.needs_human_reason = True, f"Candidate mentioned '{m.group(0)}'"

        self._scheduling(ctx, text, a)
        if a.scheduling in (SchedulingIntent.ACCEPTS_SLOT, SchedulingIntent.SHARES_AVAILABILITY) and \
                a.interest is Interest.UNCLEAR:
            a.interest = Interest.INTERESTED
        a.dealbreaker_conflicts = self._dealbreakers(ctx, text)
        a.objections = detect_objections(text)
        a.sentiment = sentiment_trend(ctx.thread, ctx.latest)
        a.at_risk, a.at_risk_reason = assess_at_risk(a.objections, a.sentiment, a.interest.value)
        a.summary = self._summary(a)
        return a

    # -- analysis helpers ---------------------------------------------------------------
    @staticmethod
    def _questions(text: str) -> list[str]:
        """Questions that need an answer - not 'maybe reach out in March?' or 'does Tue 2pm work?'."""
        out = []
        for sentence in _sentences(text):
            is_q = sentence.endswith("?") or _INFO_REQUEST.search(sentence)
            if not is_q:
                continue
            if NOT_NOW.search(sentence) or LINK.search(sentence) or _WEEKDAY_RX.search(sentence) \
                    or _TIME_RX.search(sentence):
                continue
            cleaned = _LEAD_IN.sub("", sentence).strip()
            out.append(cleaned[:1].upper() + cleaned[1:])
        return out

    @staticmethod
    def _tone(text: str) -> str:
        if re.search(r"\b(dear|regards|sincerely)\b", text, re.IGNORECASE):
            return "formal"
        if re.search(r"\b(hey|hiya|cheers|thx|lol)\b|!|:\)", text, re.IGNORECASE):
            return "casual"
        return "neutral"

    @staticmethod
    def _reason(text: str) -> str | None:
        if re.search(r"happy (where i am|in my current|at my current)|staying", text, re.IGNORECASE):
            return "staying at current company"
        if re.search(r"accepted (another|a different)", text, re.IGNORECASE):
            return "accepted another offer"
        if re.search(r"not looking", text, re.IGNORECASE):
            return "not looking right now"
        return None

    @staticmethod
    def _follow_up_date(text: str, now: datetime) -> date:
        today = now.date()
        lower = text.lower()
        for i, month in enumerate(_MONTHS, 1):
            if re.search(rf"\b{month}\b", lower):
                year = today.year + (i <= today.month)
                return date(year, i, 1)
        if m := re.search(r"in (\d+|a|one|two|three|a few|a couple of) (week|month)s?", lower):
            n = {"a": 1, "one": 1, "two": 2, "three": 3, "a few": 3, "a couple of": 2}.get(m.group(1))
            n = n or int(m.group(1))
            return today + timedelta(weeks=n) if m.group(2) == "week" else today + timedelta(days=30 * n)
        if "next quarter" in lower:
            return today + timedelta(days=90)
        if "next year" in lower or "after the holidays" in lower:
            return date(today.year + 1, 1, 12)
        return today + timedelta(days=60)

    def _scheduling(self, ctx: ConversationContext, text: str, a: ReplyAnalysis) -> None:
        if ctx.meeting and CANCEL.search(text):
            a.scheduling = SchedulingIntent.CANCEL
            return
        if ctx.meeting and RESCHEDULE.search(text):
            a.scheduling = SchedulingIntent.RESCHEDULE
            a.proposed_windows = self._windows(ctx, text)
            return
        if LINK.search(text):
            a.scheduling = SchedulingIntent.REQUESTS_LINK
            return
        if ctx.offered_slots:
            if DECLINE_SLOTS.search(text):
                a.scheduling = SchedulingIntent.SHARES_AVAILABILITY
                a.proposed_windows = self._windows(ctx, text)
                return
            idx = self._pick_offered(ctx, text)
            if idx is not None:
                a.scheduling, a.accepted_slot_index = SchedulingIntent.ACCEPTS_SLOT, idx
                return
        windows = self._windows(ctx, text)
        if windows:
            a.scheduling, a.proposed_windows = SchedulingIntent.SHARES_AVAILABILITY, windows

    def _pick_offered(self, ctx: ConversationContext, text: str) -> int | None:
        n = len(ctx.offered_slots)
        if ANY_SLOT.search(text):
            return 0
        for rx, idx in _ORDINALS:
            if rx.search(text) and idx < n:
                return idx
        if re.search(r"\b(last one|the last)\b", text, re.IGNORECASE):
            return n - 1
        mentioned = self._mentioned_weekdays(text, ctx)
        times = [self._parse_time(m) for m in _TIME_RX.finditer(text)]
        matches = []
        for i, slot in enumerate(ctx.offered_slots):
            local = ctx.local(slot.start)
            if local.weekday() in mentioned and (not times or (local.hour, local.minute) in times):
                matches.append(i)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _parse_time(m: re.Match[str]) -> tuple[int, int]:
        hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        return hour, int(m.group(2) or 0)

    def _mentioned_weekdays(self, text: str, ctx: ConversationContext) -> set[int]:
        days: set[int] = set()
        for m in _WEEKDAY_RX.finditer(text):
            token = m.group(0).lower()
            if token == "tomorrow":
                days.add((ctx.local(ctx.now).weekday() + 1) % 7)
                continue
            days.update(i for i, name in enumerate(_WEEKDAYS) if name.startswith(token[:3]))
        return days

    def _windows(self, ctx: ConversationContext, text: str) -> list[tuple[datetime, datetime]]:
        tz = ZoneInfo(a_tz) if (a_tz := detect_timezone(text)) else ZoneInfo(ctx.candidate_tz)
        windows: list[tuple[datetime, datetime]] = []
        today = ctx.now.astimezone(tz).date()
        for sentence in _sentences(text):
            days = self._mentioned_weekdays(sentence, ctx)
            if not days:
                continue
            start_t, end_t = self._time_range(sentence)
            for wd in sorted(days):
                delta = (wd - today.weekday()) % 7 or 7
                d = today + timedelta(days=delta)
                windows.append((datetime.combine(d, start_t, tz), datetime.combine(d, end_t, tz)))
        return windows

    def _time_range(self, sentence: str) -> tuple[time, time]:
        lower = sentence.lower()
        times = [self._parse_time(m) for m in _TIME_RX.finditer(sentence)]
        if "between" in lower and len(times) >= 2:
            return time(*times[0]), time(*times[1])
        if "after" in lower and times:
            return time(*times[0]), time(18)
        if "before" in lower and times:
            return time(9), time(*times[0])
        if times:
            h, mi = times[0]
            return time(h, mi), time(min(h + 1, 23), mi)
        if "morning" in lower:
            return time(9), time(12)
        if "afternoon" in lower:
            return time(12), time(17)
        if "evening" in lower:
            return time(17), time(19)
        return time(9), time(17)

    @staticmethod
    def _dealbreakers(ctx: ConversationContext, text: str) -> list[str]:
        conflicts = []
        facts = ctx.kb.facts
        if NEEDS_VISA.search(text) and any("visa" in f.topics and NEGATIVE.search(f.text) for f in facts):
            conflicts.append("Candidate needs visa sponsorship; knowledge base says it isn't offered")
        if NEEDS_REMOTE.search(text) and any(
            "location" in f.topics and re.search(r"in[- ]office|on-?site|hybrid|days a week", f.text, re.I)
            and not re.search(r"fully remote", f.text, re.I) for f in facts
        ):
            conflicts.append("Candidate wants fully remote; role is not fully remote")
        return conflicts

    @staticmethod
    def _summary(a: ReplyAnalysis) -> str:
        parts = [a.interest.value.replace("_", " ")]
        if a.questions:
            parts.append(f"{len(a.questions)} question(s)")
        if a.scheduling is not SchedulingIntent.NONE:
            parts.append(a.scheduling.value.replace("_", " "))
        if a.objections:
            parts.append("objection: " + ", ".join(a.objections))
        if a.at_risk:
            parts.append(f"at risk ({a.at_risk_reason})")
        return "; ".join(parts)

    # -- composition --------------------------------------------------------------------
    def compose(self, ctx: ConversationContext, plan: ComposePlan) -> ComposeResult:
        first = ctx.candidate.first_name
        lines: list[str] = []
        claims: list[AnswerClaim] = []
        answered_labels: list[str] = []
        deferred_labels: list[str] = []

        if plan.kind == "closing":
            body = (f"Thanks for letting me know, {first} - I appreciate you getting back to me. "
                    "I'll keep you in mind if something closer to what you're looking for comes up.")
            return ComposeResult(self._wrap(ctx, [body]), latest_activity="Sent a polite close-out")
        if plan.kind == "nudge":
            if plan.slot_labels:
                lines.append(f"Just floating this back to the top of your inbox - would any of these still work "
                             f"for a quick call about the {ctx.job_title} role?")
                lines.extend(f"- {s}" for s in plan.slot_labels)
            elif plan.scheduling_link:
                lines.append(f"Just floating this back up - if you'd still like to chat about the {ctx.job_title} "
                             f"role, you can grab any time that works here: {plan.scheduling_link}")
            else:
                lines.append(f"Just floating this back up - still happy to find a time to chat about the "
                             f"{ctx.job_title} role whenever works for you.")
            return ComposeResult(self._wrap(ctx, lines), latest_activity="Sent a follow-up nudge",
                                 open_item="waiting on candidate")
        if plan.kind == "reengage":
            lines.append(f"You'd mentioned reaching back out around now, so I wanted to check in - is the "
                         f"{ctx.job_title} role something you'd be open to exploring?")
            return ComposeResult(self._wrap(ctx, lines), latest_activity="Re-engaged after 'not now'")
        if plan.kind == "prep":
            lines.append(f"Ahead of our conversation on {plan.meeting_label}, a quick heads-up on what to expect.")
            hits = ctx.kb.search("interview process steps rounds", k=1)
            fact, score = hits[0] if hits else (None, 0.0)
            if fact is not None and score >= 0.5:
                text = re.sub(r"^[A-Z][\w &/-]{1,28}:\s+", "", ctx.kb.redact(fact.text))
                lines.append(f"How the process runs: {text.rstrip('.')}.")
                claims.append(AnswerClaim("prep:process", True, [fact.id], min(1.0, score + 0.2)))
            lines.append("Anything you'd like to make sure we cover? Just let me know.")
            return ComposeResult(self._wrap(ctx, lines), claims, latest_activity="Sent interview prep")
        if plan.kind == "confirm":
            lines.append(f"Looking forward to our conversation on {plan.meeting_label}.")
            if plan.scheduling_link:
                lines.append(f"Here are the details: {plan.scheduling_link}")
            lines.append("If anything's changed on your end, just let me know and we'll sort it out.")
            return ComposeResult(self._wrap(ctx, lines), latest_activity="Sent day-of confirmation")
        if plan.kind == "rescue":
            # One low-pressure attempt to address a recoverable concern. Facts only from the KB; if the
            # KB can't support it, offer to share more or close out - never argue, never over-promise.
            lines.append("Totally fair, and I appreciate you being upfront!" if plan.tone == "casual"
                         else "That's completely fair, and I appreciate you being upfront.")
            for code in plan.objections:
                label = OBJECTION_LABELS.get(code, code)
                hits = ctx.kb.search(label, k=1)
                fact, score = hits[0] if hits else (None, 0.0)
                if fact is not None and score >= 0.5:
                    text = re.sub(r"^[A-Z][\w &/-]{1,28}:\s+", "", ctx.kb.redact(fact.text))
                    lines.append(f"If {label} was the sticking point, one thing worth knowing: {text.rstrip('.')}.")
                    claims.append(AnswerClaim(f"objection:{code}", True, [fact.id], min(1.0, score + 0.2)))
                else:
                    lines.append(f"If {label} was the main thing, I'd be glad to share how the team thinks "
                                 "about it - or I can close this out, whichever you prefer.")
            lines.append("No pressure either way - just let me know what makes sense for you.")
            return ComposeResult(self._wrap(ctx, lines), claims,
                                 latest_activity="Attempted to recover a soft no", open_item="awaiting candidate")

        if plan.kind == "follow_up_answer":
            for question, answer in plan.recruiter_answers:
                lines.append(f"Following up on your question about {question_label(question)}: {answer.strip()}")
                claims.append(AnswerClaim(question, True, ["recruiter"], 1.0))
                answered_labels.append(question_label(question))
        else:
            lines.append("Thanks so much for getting back to me!" if plan.tone == "casual"
                         else "Thanks for getting back to me.")

        for label in plan.acknowledge:  # hard concerns: acknowledge, hand to the recruiter, never negotiate
            if label == "your note":
                lines.append(f"Thanks for your note - let me loop in {ctx.recruiter.first_name} directly "
                             "so you get the right answer on this.")
            else:
                lines.append(f"Thanks for being upfront about {label} - that's an important one, so let me "
                             f"bring in {ctx.recruiter.first_name} to go through it with you properly.")

        for code in plan.objections:  # acknowledge soft concerns warmly; facts only from the KB
            label = OBJECTION_LABELS.get(code, code)
            hits = ctx.kb.search(label, k=1)
            fact, score = hits[0] if hits else (None, 0.0)
            if fact is not None and score >= 0.5:
                text = re.sub(r"^[A-Z][\w &/-]{1,28}:\s+", "", ctx.kb.redact(fact.text))
                lines.append(f"I hear you on {label} - for what it's worth, {text.rstrip('.')}.")
                claims.append(AnswerClaim(f"objection:{code}", True, [fact.id], min(1.0, score + 0.2)))
            else:
                lines.append(f"I hear you on {label} - happy to talk it through and get you the details "
                             "that matter most to you.")

        for question in plan.questions:
            label = question_label(question)
            hits = ctx.kb.search(question, k=1)
            fact, score = hits[0] if hits else (None, 0.0)
            if question not in plan.force_defer and fact is not None and score >= 0.45 \
                    and fact.topics & topics_of(question):
                text = re.sub(r"^[A-Z][\w &/-]{1,28}:\s+", "", ctx.kb.redact(fact.text))  # drop "Heading: "
                lines.append(f"On {label}: {text.rstrip('.')}.")
                claims.append(AnswerClaim(question, True, [fact.id], min(1.0, score + 0.2)))
                answered_labels.append(label)
            else:
                lines.append(f"On {label}: let me confirm that with the team and get back to you shortly.")
                claims.append(AnswerClaim(question, False, [], 0.0))
                deferred_labels.append(label)

        if NOTE_SLOT_TAKEN in plan.notes:
            lines.append("Apologies - that time was just taken on my end.")
        if plan.meeting_cancelled:
            lines.append("No problem at all - I've taken our call off the calendar.")
        if plan.confirmed_meeting:
            lines.append(f"You're all set for {plan.confirmed_meeting}. I've sent over a calendar invite.")
        elif plan.slot_labels:
            lines.append("Would any of these times work for a quick intro call?")
            lines.extend(f"- {s}" for s in plan.slot_labels)
        elif plan.scheduling_link:
            lines.append(f"Feel free to grab whatever time works best for you here: {plan.scheduling_link}")
        if plan.follow_up_label:
            lines.append(f"Totally understand - I'll reach back out around {plan.follow_up_label}.")

        if plan.confirmed_meeting:
            activity, open_item = f"Booked interview for {plan.confirmed_meeting}", None
        elif plan.slot_labels:
            activity, open_item = "Sent available times for screening call", f"offered {len(plan.slot_labels)} times"
        elif plan.scheduling_link:
            activity, open_item = "Sent scheduling link", "waiting for candidate to book"
        elif answered_labels:
            activity, open_item = f"Answered questions about {', '.join(answered_labels)}", None
        elif plan.follow_up_label:
            activity, open_item = f"Not now - follow up {plan.follow_up_label}", None
        else:
            activity, open_item = "Replied to candidate", None
        if deferred_labels:
            open_item = f"checking with recruiter about {', '.join(deferred_labels)}"
        return ComposeResult(self._wrap(ctx, lines), claims, activity, open_item)

    @staticmethod
    def _wrap(ctx: ConversationContext, lines: list[str]) -> str:
        style = getattr(ctx.recruiter, "style", None) or DEFAULT_STYLE
        greeting = style.render_greeting(ctx.candidate.first_name)
        signoff = style.render_signoff(ctx.recruiter.first_name)
        return f"{greeting}\n\n" + "\n".join(lines) + f"\n\n{signoff}"

    def distill_fact(self, ctx: ConversationContext, question: str, answer: str) -> DistilledFact:
        if re.search(r"don'?t (tell|share)|internal only|confidential|between us|off the record", answer, re.I):
            return DistilledFact(None, shareable=False)
        scope = "company" if re.search(r"company-?wide|all (roles|positions|jobs)|everyone at", answer, re.I) \
            else "job"
        return DistilledFact(answer.strip().rstrip(".") + ".", scope=scope)
