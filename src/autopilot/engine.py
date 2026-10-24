"""The reply agent.

Entry points (all driven by external events or a periodic tick):

    record_outreach(...)       the sequence sent a message; thread is now watched
    handle_inbound(msg)        a message arrived on a watched thread
    on_recruiter_answer(q, a)  recruiter answered an open question (e.g. Slack thread reply)
    approve_draft(item)        recruiter sent a draft we prepared
    record_human_reply(...)    recruiter wrote in the thread themselves
    move_card(card, stage)     recruiter dragged the card
    tick()                     send due replies; nudges, auto-archive, meeting completion, re-engagement

Per inbound message:  filter -> open card -> analyze -> policy -> compose -> verify -> deliver.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from autopilot import guardrails
from autopilot.insights import HARD_OBJECTIONS, OBJECTION_LABELS, SOFT_OBJECTIONS
from autopilot.brain.base import (
    NOTE_SLOT_TAKEN,
    Brain,
    ComposePlan,
    ComposeResult,
    ConversationContext,
    ReplyAnalysis,
    SchedulingIntent,
)
from autopilot.brain.text import looks_automated
from autopilot.clock import Clock, SystemClock
from autopilot.integrations.calendar import Calendar
from autopilot.integrations.mail import MailProvider
from autopilot.integrations.slack import Notifier
from autopilot.knowledge import KnowledgeBase
from autopilot.models import (
    ArchiveReason,
    AuditEntry,
    Candidate,
    Card,
    Direction,
    Effect,
    InboxEvent,
    Interest,
    InterviewStatus,
    Job,
    Meeting,
    Message,
    OfferedSlot,
    OpenQuestion,
    OutboxItem,
    OutboxStatus,
    Stage,
)
from autopilot.pipeline import REOPENABLE, can_auto_transition, is_active
from autopilot.scheduling import format_slot, format_slot_dual, propose_slots, slot_is_free
from autopilot.settings import AutomationSettings, EscalationChannel, ReplySpeed, ScheduleMode, resolve_settings
from autopilot.store import Store


class Autopilot:
    def __init__(self, store: Store, brain: Brain, mail: MailProvider, calendar: Calendar, notifier: Notifier,
                 clock: Clock | None = None, team_settings: AutomationSettings | None = None,
                 rng: random.Random | None = None):
        self.store = store
        self.brain = brain
        self.mail = mail
        self.calendar = calendar
        self.notifier = notifier
        self.clock = clock or SystemClock()
        self.team_settings = team_settings or AutomationSettings()
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------ bookkeeping
    def settings_for(self, job: Job) -> AutomationSettings:
        return resolve_settings(self.team_settings, job.settings_override)

    def _audit(self, card: Card | None, action: str, detail: str, reasoning: str = "") -> None:
        self.store.audit.append(AuditEntry(self.clock.now(), card.id if card else None, action, detail, reasoning))

    def _event(self, card: Card, text: str) -> None:
        self.store.events.append(InboxEvent(card.id, self.clock.now(), text))

    def _parts(self, card: Card) -> tuple[Candidate, Job]:
        return self.store.candidates[card.candidate_id], self.store.jobs[card.job_id]

    def _kb(self, job: Job) -> KnowledgeBase:
        company = self.store.companies[job.company_id]
        return KnowledgeBase(company, job, self.store.learned.for_job(job))

    def _candidate_tz(self, card: Card) -> str:
        candidate, job = self._parts(card)
        return card.candidate_timezone or candidate.timezone or self.store.recruiters[job.owner_id].timezone

    def _context(self, card: Card, latest: Message | None) -> ConversationContext:
        candidate, job = self._parts(card)
        company = self.store.companies[job.company_id]
        return ConversationContext(
            recruiter=self.store.recruiters[job.owner_id],
            candidate=candidate,
            job_title=job.title,
            company_display_name="our client" if company.hide_details else company.name,
            job_description=job.description,
            kb=self._kb(job),
            thread=list(self.store.threads.get(card.thread_id, [])),
            latest=latest,
            now=self.clock.now(),
            candidate_tz=self._candidate_tz(card),
            offered_slots=list(card.offered_slots),
            meeting=card.meeting,
        )

    def _move(self, card: Card, stage: Stage, why: str) -> None:
        if not can_auto_transition(card, stage):
            return
        candidate, _ = self._parts(card)
        before = card.stage
        card.stage = stage
        if stage is not Stage.ARCHIVED:
            card.archive_reason = None
        if stage is Stage.INTERVIEWING and card.interview_status is None:
            card.interview_status = InterviewStatus.NOT_SCHEDULED
        self._event(card, f"{candidate.name} moved to {stage.value}")
        self._audit(card, "stage", f"{before.value} -> {stage.value}", why)

    def _archive(self, card: Card, reason: ArchiveReason, detail: str, why: str) -> None:
        for item in self.store.pending_outbox(card.id):
            self._cancel(item, "candidate archived")
        self._move(card, Stage.ARCHIVED, why)
        card.archive_reason = reason
        card.latest_activity = detail
        card.open_item = None

    # ------------------------------------------------------------------ outreach + inbound
    def record_outreach(self, candidate: Candidate, job: Job, subject: str, body: str,
                        thread_id: str | None = None) -> str:
        thread_id = thread_id or self.store.new_id("thr")
        recruiter = self.store.recruiters[job.owner_id]
        self.store.thread_owner[thread_id] = (candidate.id, job.id)
        self.store.threads.setdefault(thread_id, []).append(Message(
            self.store.new_id("m"), thread_id, Direction.OUTBOUND, recruiter.email, candidate.email,
            subject, body, self.clock.now(), from_sequence=True))
        return thread_id

    def handle_inbound(self, msg: Message) -> Card | None:
        owner = self.store.thread_owner.get(msg.thread_id)
        if owner is None:
            self._audit(None, "ignore", f"message on unknown thread {msg.thread_id}")
            return None
        candidate = self.store.candidates[owner[0]]
        job = self.store.jobs[owner[1]]
        self.store.threads[msg.thread_id].append(msg)
        card = self.store.card_for_thread(msg.thread_id)

        if looks_automated(msg):
            self._audit(card, "ignore", "automated message (auto-reply/bounce)",
                        "only genuine candidate replies open a card or count as responses")
            return card

        if card is None:
            card = Card(self.store.new_id("card"), candidate.id, job.id, msg.thread_id)
            self.store.cards[card.id] = card
            self.store.responded.add(candidate.id)  # halts every active sequence for this candidate
            self._event(card, "Candidate responded to outreach sequence")
            self._audit(card, "open", "card opened in Replied", "first genuine reply to outreach")
        card.last_inbound_at = msg.sent_at

        for item in self.store.pending_outbox(card.id):
            self._cancel(item, "superseded by a newer candidate message")

        if candidate.email.lower() in self.store.suppressed:
            self._audit(card, "ignore", "candidate is unsubscribed")
            return card
        if card.paused or card.stage is Stage.HIRED or (
                card.stage is Stage.ARCHIVED and card.archive_reason not in REOPENABLE):
            self.notifier.status_update(self.store.recruiters[job.owner_id],
                                        f"{candidate.name} replied on {job.title} (you're handling this one).")
            self._audit(card, "skip", "recruiter owns this conversation")
            return card

        ctx = self._context(card, msg)
        analysis = self.brain.analyze(ctx)
        if analysis.is_auto_reply:
            self._audit(card, "ignore", "model classified message as automated")
            return card
        if analysis.candidate_timezone:
            try:
                ZoneInfo(analysis.candidate_timezone)
                card.candidate_timezone = analysis.candidate_timezone
                ctx.candidate_tz = analysis.candidate_timezone
            except (ZoneInfoNotFoundError, ValueError):
                pass
        self._audit(card, "analyze", analysis.summary or analysis.interest.value,
                    f"interest={analysis.interest.value} scheduling={analysis.scheduling.value} "
                    f"questions={len(analysis.questions)} needs_human={analysis.needs_human}")
        self._respond(card, ctx, analysis)
        return card

    # ------------------------------------------------------------------ policy
    def _respond(self, card: Card, ctx: ConversationContext, a: ReplyAnalysis) -> None:
        candidate, job = self._parts(card)
        recruiter = self.store.recruiters[job.owner_id]
        settings = self.settings_for(job)
        review: list[str] = []

        # Candidate-experience signals: record on the card, escalate money/competing situations to a
        # human, and raise a one-time at-risk heads-up before a good candidate slips away.
        card.sentiment = a.sentiment
        card.objections = sorted(set(card.objections) | set(a.objections))
        card.at_risk, card.at_risk_reason = a.at_risk, a.at_risk_reason
        hard = [o for o in a.objections if o in HARD_OBJECTIONS]
        if hard and not a.needs_human:
            a.needs_human = True
            a.needs_human_reason = a.at_risk_reason or "raised a compensation or competing-offer concern"
        # Proactive at-risk heads-up for the soft/cooling case; the needs_human path has its own alert.
        if a.at_risk and not hard and not card.at_risk_alerted and a.interest is not Interest.NOT_INTERESTED:
            card.at_risk_alerted = True
            self.notifier.status_update(
                recruiter, f":warning: {candidate.name} ({job.title}) may be at risk - {a.at_risk_reason}. "
                           f"Sentiment is {a.sentiment}.")

        if a.unsubscribe:
            self.store.suppressed.add(candidate.email.lower())
            self._archive(card, ArchiveReason.UNSUBSCRIBED, "Unsubscribed", "candidate asked not to be contacted")
            self.notifier.status_update(recruiter, f"{candidate.name} unsubscribed from {job.title}; archived.")
            return
        if a.offer_status == "accepted":
            self._move(card, Stage.HIRED, "candidate accepted the offer")
            card.latest_activity, card.open_item = "Accepted the offer", None
            self.notifier.status_update(recruiter, f":tada: {candidate.name} accepted the offer for {job.title}.")
            return
        if a.offer_status == "declined":
            self._archive(card, ArchiveReason.OFFER_DECLINED, "Declined the offer", "candidate declined the offer")
            self.notifier.status_update(recruiter, f"{candidate.name} declined the offer for {job.title}.")
            return
        if a.needs_human:
            review.append(a.needs_human_reason or "needs a human")
            self.notifier.status_update(
                recruiter, f"{candidate.name} ({job.title}) needs your attention: {a.needs_human_reason}. "
                           "I've drafted a reply for you to review.")

        if a.interest is Interest.NOT_INTERESTED:
            card.interest = Interest.NOT_INTERESTED
            if settings.archive_not_interested:
                reason = a.not_interested_reason or "not interested"
                self._archive(card, ArchiveReason.NOT_INTERESTED, f"Declined - {reason}",
                              "candidate said they are not interested")
                if settings.close_politely:
                    self._compose_and_deliver(card, ctx, ComposePlan(kind="closing", tone=a.tone), settings,
                                              review, [], a.degraded, keep_card_summary=True)
            else:
                card.latest_activity, card.open_item = "Said not interested", "decide whether to archive"
            return

        plan = ComposePlan(kind="reply", questions=list(a.questions), tone=a.tone,
                           objections=[o for o in a.objections if o in SOFT_OBJECTIONS])
        plan.acknowledge = [OBJECTION_LABELS[o] for o in hard]
        if a.needs_human and not plan.acknowledge and not plan.questions:
            plan.acknowledge = ["your note"]  # a holding draft so the recruiter has something to send
        effects: list[Effect] = []

        if a.interest is Interest.NOT_NOW:
            card.interest = Interest.NOT_NOW
            follow = a.follow_up_on or (ctx.now.date() + timedelta(days=60))
            card.follow_up_on = datetime.combine(follow, time(10), ZoneInfo(ctx.candidate_tz))
            plan.follow_up_label = f"{follow:%B} {follow.day}"
        else:
            if a.interest is Interest.INTERESTED:
                card.interest = Interest.INTERESTED
            self._move(card, Stage.QUALIFYING, "candidate engaged with the conversation")
            self._plan_scheduling(card, ctx, a, settings, plan, effects)

        if a.dealbreaker_conflicts:
            review.extend(a.dealbreaker_conflicts)
            self.notifier.status_update(recruiter, f"Possible deal-breaker for {candidate.name} ({job.title}): "
                                                   + "; ".join(a.dealbreaker_conflicts))

        if not (plan.questions or plan.slot_labels or plan.scheduling_link or plan.confirmed_meeting
                or plan.meeting_cancelled or plan.follow_up_label or plan.objections or plan.acknowledge):
            card.latest_activity = card.latest_activity or "Candidate replied"
            self._audit(card, "no_reply", "nothing to say; waiting", a.summary)
            return
        self._compose_and_deliver(card, ctx, plan, settings, review, effects, a.degraded)

    def _plan_scheduling(self, card: Card, ctx: ConversationContext, a: ReplyAnalysis,
                         settings: AutomationSettings, plan: ComposePlan, effects: list[Effect]) -> None:
        candidate, job = self._parts(card)
        recruiter = self.store.recruiters[job.owner_id]
        sched = settings.scheduling
        qualified = card.interest is Interest.INTERESTED and not a.dealbreaker_conflicts and not a.needs_human
        intent = a.scheduling

        if intent is SchedulingIntent.CANCEL and card.meeting:
            effects.append(Effect("cancel_meeting"))
            plan.meeting_cancelled = True
            return
        if sched.mode is ScheduleMode.NONE:
            if intent is not SchedulingIntent.NONE or (qualified and not card.meeting):
                self.notifier.status_update(recruiter, f"{candidate.name} ({job.title}) is ready to schedule - "
                                                       "scheduling is set to manual.")
            return
        if sched.mode is ScheduleMode.LINK:
            if sched.scheduling_link and (intent is SchedulingIntent.REQUESTS_LINK or
                                          (qualified and not card.scheduling_link_sent and not card.meeting)):
                plan.scheduling_link = sched.scheduling_link
                effects.append(Effect("link_sent"))
                self._move(card, Stage.INTERVIEWING, "sent scheduling link to a qualified candidate")
            return

        # ScheduleMode.BOOK
        if not qualified and intent is not SchedulingIntent.RESCHEDULE:
            return
        tz = ctx.candidate_tz
        if intent is SchedulingIntent.RESCHEDULE and card.meeting:
            effects.append(Effect("cancel_meeting"))
            plan.meeting_cancelled = True
            self._offer(card, ctx, settings, plan, effects, a.proposed_windows or None)
            return
        if card.meeting:
            return
        if intent is SchedulingIntent.ACCEPTS_SLOT and a.accepted_slot_index is not None:
            slot = card.offered_slots[a.accepted_slot_index]
            if slot_is_free(sched, self.calendar, slot):
                plan.confirmed_meeting = format_slot_dual(slot.start, tz, recruiter.timezone)
                effects.append(Effect("book_meeting", slot=slot))
            else:
                plan.notes.append(NOTE_SLOT_TAKEN)
                self._offer(card, ctx, settings, plan, effects, None, after=slot.start)
            self._move(card, Stage.INTERVIEWING, "candidate is choosing an interview time")
            return
        if intent is SchedulingIntent.SHARES_AVAILABILITY and a.proposed_windows:
            exact = [w for w in a.proposed_windows
                     if w[1] - w[0] <= timedelta(minutes=sched.duration_minutes + 30)]
            for start, _ in exact:
                slot = OfferedSlot(start, start + timedelta(minutes=sched.duration_minutes))
                fits = any(s.start == slot.start for s in propose_slots(
                    sched, self.calendar, ctx.now, recruiter.timezone, [(slot.start, slot.end)], count=1))
                if fits:
                    plan.confirmed_meeting = format_slot_dual(slot.start, tz, recruiter.timezone)
                    effects.append(Effect("book_meeting", slot=slot))
                    self._move(card, Stage.INTERVIEWING, "candidate proposed a time that is free")
                    return
            self._offer(card, ctx, settings, plan, effects, a.proposed_windows)
            return
        if intent is SchedulingIntent.SHARES_AVAILABILITY or not card.offered_slots:
            after = max((s.start for s in card.offered_slots), default=None)
            self._offer(card, ctx, settings, plan, effects, None, after=after)

    def _offer(self, card: Card, ctx: ConversationContext, settings: AutomationSettings, plan: ComposePlan,
               effects: list[Effect], windows: list[tuple[datetime, datetime]] | None,
               after: datetime | None = None) -> None:
        _, job = self._parts(card)
        recruiter = self.store.recruiters[job.owner_id]
        start_from = ctx.now
        if after is not None:  # offer days after the ones already offered/taken
            tz = ZoneInfo(recruiter.timezone)
            next_day = datetime.combine(after.astimezone(tz).date() + timedelta(days=1), time(0), tz)
            start_from = max(ctx.now, next_day - timedelta(hours=settings.scheduling.min_notice_hours))
        slots = propose_slots(settings.scheduling, self.calendar, start_from, recruiter.timezone, windows,
                              candidate_tz=ctx.candidate_tz)
        if windows and not slots:  # nothing inside their windows: offer our next best times
            slots = propose_slots(settings.scheduling, self.calendar, start_from, recruiter.timezone,
                                  candidate_tz=ctx.candidate_tz)
        if not slots:
            self.notifier.status_update(recruiter, f"No free interview slots in the next "
                                                   f"{settings.scheduling.horizon_days} days for {job.title}.")
            return
        plan.slot_labels = [format_slot_dual(s.start, ctx.candidate_tz, recruiter.timezone) for s in slots]
        effects.append(Effect("offered_slots", slots=slots))
        self._move(card, Stage.INTERVIEWING, "offered interview times to a qualified candidate")

    # ------------------------------------------------------------------ compose / verify / deliver
    def _compose_and_deliver(self, card: Card, ctx: ConversationContext, plan: ComposePlan,
                             settings: AutomationSettings, review: list[str], effects: list[Effect],
                             degraded: bool, keep_card_summary: bool = False,
                             send_at: datetime | None = None) -> OutboxItem:
        candidate, job = self._parts(card)
        company = self.store.companies[job.company_id]
        recruiter_answers = [answer for _, answer in plan.recruiter_answers]
        allow_recruiter = bool(plan.recruiter_answers)

        result = self.brain.compose(ctx, plan)
        bad = guardrails.unverified_answers(result, ctx.kb, plan.questions, settings.min_answer_confidence,
                                            allow_recruiter)
        if bad - plan.force_defer:
            plan.force_defer |= bad
            self._audit(card, "verify", f"re-writing with {len(bad)} unverified answer(s) deferred",
                        "; ".join(sorted(bad)))
            result = self.brain.compose(ctx, plan)
            if guardrails.unverified_answers(result, ctx.kb, plan.questions, settings.min_answer_confidence,
                                             allow_recruiter) - plan.force_defer:
                review.append("answers could not be verified against the knowledge base")
        degraded = degraded or result.degraded
        issues = guardrails.check_text(result.body, ctx.kb, company, recruiter_answers)
        if issues:
            review.extend(issues)
            self._audit(card, "guardrail", "; ".join(issues), "message held for human review")

        unanswered = [q for q in plan.questions
                      if q in plan.force_defer or not any(c.question == q and c.answered for c in result.answers)]
        for question in unanswered:
            self._open_question(card, question, settings)

        body = result.body
        if settings.ai_disclosure_footer:
            body += "\n\n" + settings.ai_disclosure_footer
        item = self._deliver(card, plan.kind, body, effects, settings, review, degraded, result, send_at)
        if not keep_card_summary:
            activity = result.latest_activity or card.latest_activity
            card.latest_activity = f"Draft ready: {activity}" if item.status is OutboxStatus.DRAFT else activity
            card.open_item = result.open_item
        return item

    def _deliver(self, card: Card, kind: str, body: str, effects: list[Effect], settings: AutomationSettings,
                 review: list[str], degraded: bool, result: ComposeResult | None = None,
                 send_at: datetime | None = None) -> OutboxItem:
        candidate, job = self._parts(card)
        now = self.clock.now()
        subject = self._reply_subject(card)
        blockers = guardrails.auto_send_blockers(
            card, settings, now, self.store.agent_sends_since(card.thread_id, now - timedelta(days=1)), degraded)
        reasons = review + blockers
        item = OutboxItem(self.store.new_id("out"), card.id, card.thread_id, candidate.email, subject, body, kind,
                          now, OutboxStatus.DRAFT, effects=effects, review_reasons=reasons,
                          latest_activity=result.latest_activity if result else "",
                          open_item=result.open_item if result else None)
        if not reasons:
            item.status = OutboxStatus.SCHEDULED
            item.send_at = send_at or now + self._reply_delay(settings.reply_speed)
            self._audit(card, "schedule_send", f"{kind} at {item.send_at.isoformat()}",
                        f"reply speed={settings.reply_speed.value}")
        else:
            item.draft_ref = self.mail.create_draft(card.thread_id, candidate.email, subject, body)
            self._audit(card, "draft", f"{kind} drafted for review", "; ".join(reasons))
        self.store.outbox[item.id] = item
        return item

    def _reply_subject(self, card: Card) -> str:
        first = next((m.subject for m in self.store.threads.get(card.thread_id, []) if m.subject), "")
        return first if first.lower().startswith("re:") else f"Re: {first}"

    def _reply_delay(self, speed: ReplySpeed) -> timedelta:
        if speed is ReplySpeed.INSTANT:
            return timedelta(seconds=self.rng.uniform(5, 20))
        return timedelta(seconds=self.rng.uniform(180, 540))

    def _cancel(self, item: OutboxItem, why: str) -> None:
        if item.draft_ref:
            self.mail.delete_draft(item.draft_ref)
        item.status = OutboxStatus.CANCELLED
        self._audit(self.store.cards.get(item.card_id), "cancel", f"{item.kind} cancelled", why)

    def _send(self, item: OutboxItem, by_agent: bool = True, body: str | None = None) -> bool:
        card = self.store.cards[item.card_id]
        candidate, job = self._parts(card)
        recruiter = self.store.recruiters[job.owner_id]
        settings = self.settings_for(job)
        for effect in item.effects:  # re-validate time-sensitive effects right before sending
            if effect.kind == "book_meeting" and effect.slot and not slot_is_free(settings.scheduling,
                                                                                   self.calendar, effect.slot):
                self._cancel(item, "chosen slot was taken before the confirmation went out")
                self._replan_slot_taken(card, effect.slot)
                return False
        body = body if body is not None else item.body
        if item.draft_ref:
            self.mail.delete_draft(item.draft_ref)
        self.mail.send(item.thread_id, item.to, item.subject, body)
        now = self.clock.now()
        self.store.threads[item.thread_id].append(Message(
            self.store.new_id("m"), item.thread_id, Direction.OUTBOUND, recruiter.email, item.to, item.subject,
            body, now, sent_by_agent=by_agent))
        item.status, item.body = OutboxStatus.SENT, body
        card.last_outbound_at = now
        if item.latest_activity and item.kind != "closing":
            card.latest_activity, card.open_item = item.latest_activity, item.open_item
        if by_agent:
            self._event(card, f"Sent by agent: {item.kind}")
        for effect in item.effects:
            self._apply_effect(card, effect, settings)
        return True

    def _apply_effect(self, card: Card, effect: Effect, settings: AutomationSettings) -> None:
        candidate, job = self._parts(card)
        recruiter = self.store.recruiters[job.owner_id]
        if effect.kind == "offered_slots":
            card.offered_slots = list(effect.slots)
        elif effect.kind == "link_sent":
            card.scheduling_link_sent = True
        elif effect.kind == "cancel_meeting" and card.meeting:
            self.calendar.cancel_event(card.meeting.event_id)
            card.meeting = None
            card.interview_status = InterviewStatus.NOT_SCHEDULED
            self.notifier.status_update(recruiter, f"Cancelled the interview with {candidate.name} ({job.title}).")
        elif effect.kind == "book_meeting" and effect.slot:
            slot = effect.slot
            attendees = [candidate.email, recruiter.email, *settings.scheduling.attendees]
            event_id = self.calendar.create_event(f"{candidate.name} / {recruiter.name} - {job.title}",
                                                  slot.start, slot.end, attendees)
            card.meeting = Meeting(slot.start, slot.end, event_id)
            card.offered_slots = []
            card.interview_status = InterviewStatus.SCHEDULED
            self._move(card, Stage.INTERVIEWING, "interview booked")
            label = format_slot(slot.start, recruiter.timezone)
            card.latest_activity, card.open_item = f"Booked interview for {label}", None
            self._event(card, f"Interview booked for {label}")
            self.notifier.status_update(recruiter, f":calendar: Booked {candidate.name} ({job.title}) for {label}.")

    def _replan_slot_taken(self, card: Card, slot: OfferedSlot) -> None:
        _, job = self._parts(card)
        settings = self.settings_for(job)
        ctx = self._context(card, None)
        plan = ComposePlan(kind="reply", notes=[NOTE_SLOT_TAKEN])
        effects: list[Effect] = []
        self._offer(card, ctx, settings, plan, effects, None, after=slot.start)
        if plan.slot_labels:
            self._compose_and_deliver(card, ctx, plan, settings, [], effects, False)

    def _open_question(self, card: Card, question: str, settings: AutomationSettings) -> None:
        if any(q.question == question for q in self.store.open_questions(card.id)):
            return
        candidate, job = self._parts(card)
        recruiter = self.store.recruiters[job.owner_id]
        q = OpenQuestion(self.store.new_id("q"), card.id, question, self.clock.now())
        self.store.questions[q.id] = q
        context = f"{candidate.name} · {job.title}"
        if settings.escalation is EscalationChannel.SLACK:
            q.notifier_ref = self.notifier.ask(recruiter, q, context)
        self._refresh_home(recruiter.id)
        self._audit(card, "escalate", question, "not answerable from the knowledge base")

    def _refresh_home(self, recruiter_id: str) -> None:
        recruiter = self.store.recruiters[recruiter_id]
        pending = []
        for q in self.store.open_questions():
            card = self.store.cards[q.card_id]
            candidate, job = self._parts(card)
            if job.owner_id == recruiter_id:
                pending.append((q, f"{candidate.name} · {job.title}"))
        self.notifier.refresh(recruiter, pending)

    # ------------------------------------------------------------------ recruiter-side events
    def on_recruiter_answer(self, question_id: str, answer: str) -> OutboxItem | None:
        q = self.store.questions.get(question_id)
        if q is None or not q.is_open:
            return None
        q.answer, q.answered_at = answer, self.clock.now()
        card = self.store.cards[q.card_id]
        candidate, job = self._parts(card)
        settings = self.settings_for(job)
        ctx = self._context(card, None)
        review: list[str] = []
        if settings.allow_learning:
            fact = self.brain.distill_fact(ctx, q.question, answer)
            if fact.text and fact.shareable:
                learned = self.store.learned.add(job, fact.text, q.question, fact.scope, self.clock.now())
                self._audit(card, "learn", f"[{learned.id}] {learned.text}", f"scope={fact.scope}")
            elif not fact.shareable:
                review.append("recruiter's answer may be internal-only")
        self._refresh_home(job.owner_id)
        if not is_active(card):
            return None
        plan = ComposePlan(kind="follow_up_answer", recruiter_answers=[(q.question, answer)])
        item = self._compose_and_deliver(card, self._context(card, None), plan, settings, review, [], False)
        remaining = self.store.open_questions(card.id)
        if remaining:
            card.open_item = f"waiting on {len(remaining)} more answer(s)"
        return item

    def on_slack_reply(self, ref: str, text: str) -> OutboxItem | None:
        q = next((q for q in self.store.questions.values() if q.notifier_ref == ref and q.is_open), None)
        return self.on_recruiter_answer(q.id, text) if q else None

    def approve_draft(self, item_id: str, edited_body: str | None = None) -> bool:
        """The recruiter approved a draft: send it (possibly edited) and run its deferred effects."""
        item = self.store.outbox[item_id]
        if item.status is not OutboxStatus.DRAFT:
            return False
        return self._send(item, by_agent=False, body=edited_body)

    def record_human_reply(self, thread_id: str, body: str) -> None:
        card = self.store.card_for_thread(thread_id)
        if card is None:
            return
        candidate, job = self._parts(card)
        recruiter = self.store.recruiters[job.owner_id]
        now = self.clock.now()
        for item in self.store.pending_outbox(card.id):
            self._cancel(item, "recruiter replied manually")
        self.store.threads[thread_id].append(Message(self.store.new_id("m"), thread_id, Direction.OUTBOUND,
                                                     recruiter.email, candidate.email, "", body, now))
        card.last_outbound_at = card.last_human_outbound_at = now
        self._audit(card, "human_reply", "recruiter wrote in thread", "agent drafts only for a while")

    def move_card(self, card_id: str, stage: Stage, reason: ArchiveReason | None = None) -> None:
        card = self.store.cards[card_id]
        candidate, _ = self._parts(card)
        before = card.stage
        card.stage, card.manually_moved_at = stage, self.clock.now()
        card.archive_reason = reason if stage is Stage.ARCHIVED else None
        if stage is Stage.INTERVIEWING and card.interview_status is None:
            card.interview_status = InterviewStatus.NOT_SCHEDULED
        self._event(card, f"{candidate.name} moved to {stage.value}")
        self._audit(card, "manual_move", f"{before.value} -> {stage.value}", "recruiter override")

    # ------------------------------------------------------------------ timers
    def tick(self) -> None:
        now = self.clock.now()
        for item in sorted(self.store.outbox.values(), key=lambda o: o.send_at or now):
            if item.status is OutboxStatus.SCHEDULED and item.send_at and item.send_at <= now:
                self._send(item)
        for card in list(self.store.cards.values()):
            self._timers(card, now)

    def _timers(self, card: Card, now: datetime) -> None:
        if not is_active(card):
            return
        candidate, job = self._parts(card)
        recruiter = self.store.recruiters[job.owner_id]
        settings = self.settings_for(job)

        if card.meeting and card.meeting.end <= now and card.interview_status is InterviewStatus.SCHEDULED:
            card.interview_status = InterviewStatus.INTERVIEWED
            card.latest_activity, card.open_item = "Interview completed", "awaiting your feedback"
            self._event(card, "Interview completed")
            self.notifier.status_update(recruiter, f"Interview with {candidate.name} ({job.title}) is done.")
            return
        if card.meeting:
            return
        pending = self.store.pending_outbox(card.id)
        if any(o.status is OutboxStatus.SCHEDULED for o in pending):
            return

        if card.follow_up_on and now >= card.follow_up_on and not pending:
            card.follow_up_on = None
            card.interest = Interest.UNCLEAR
            ctx = self._context(card, None)
            self._compose_and_deliver(card, ctx, ComposePlan(kind="reengage"), settings,
                                      ["re-engagement after 'not now' is always reviewed"], [], False)
            return
        if card.interest is Interest.NOT_NOW:
            return

        waiting = card.last_outbound_at and (card.last_inbound_at is None or card.last_outbound_at > card.last_inbound_at)
        if not waiting:
            return
        idle = now - card.last_outbound_at
        if (not pending and card.interest is Interest.INTERESTED and settings.nudge_after_days is not None
                and card.nudges_sent < settings.max_nudges and idle >= timedelta(days=settings.nudge_after_days)):
            ctx = self._context(card, None)
            send_at = self._local_morning(now, ctx.candidate_tz)
            plan = ComposePlan(kind="nudge", scheduling_link=settings.scheduling.scheduling_link
                               if card.scheduling_link_sent else None)
            effects: list[Effect] = []
            fresh = [s for s in card.offered_slots if s.start > send_at + timedelta(hours=12)]
            if fresh:
                plan.slot_labels = [format_slot_dual(s.start, ctx.candidate_tz, recruiter.timezone) for s in fresh]
            elif card.offered_slots and settings.scheduling.mode is ScheduleMode.BOOK:
                self._offer(card, ctx, settings, plan, effects, None)  # earlier times expired: offer new ones
            card.nudges_sent += 1
            self._compose_and_deliver(card, ctx, plan, settings, [], effects, False, send_at=send_at)
            return
        if settings.archive_unresponsive_after_days is not None and \
                idle >= timedelta(days=settings.archive_unresponsive_after_days):
            self._archive(card, ArchiveReason.UNRESPONSIVE, "Archived - no reply",
                          f"no reply for {idle.days} days")

    def _local_morning(self, now: datetime, tz: str) -> datetime:
        """Next weekday 9-11am in the candidate's timezone (or now if we're already in it)."""
        local = now.astimezone(ZoneInfo(tz))
        if local.weekday() < 5 and 9 <= local.hour < 11:
            return now
        day: date = local.date() + timedelta(days=0 if local.hour < 9 else 1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        start = datetime.combine(day, time(9), ZoneInfo(tz))
        return start + timedelta(minutes=self.rng.uniform(0, 110))
