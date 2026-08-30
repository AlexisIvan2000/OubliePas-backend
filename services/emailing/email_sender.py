import asyncio
from decimal import Decimal
from functools import partial
from typing import Dict

import resend

from core.config import (
    FRONTEND_URL,
    RESEND_API_KEY,
    RESEND_FROM_EMAIL,
    RESEND_FROM_EMAIL_REMINDER,
    RESEND_FROM_NAME,
)
from services.emailing import layout, messages

SETTINGS_URL = f"{FRONTEND_URL}/rappels"

CODE_MAILS = {
    "verify": ("verify_subject", "verify_title", "verify_intro", "verify_ignore"),
    "reset": ("reset_subject", "reset_title", "reset_intro", "reset_ignore"),
    "change": ("change_subject", "change_title", "change_intro", "change_ignore"),
}


def _paragraphs(text: str) -> str:
    # Un saut simple reste un saut de ligne, un saut double ouvre un paragraphe :
    # le texte est ecrit a la main dans l'outil d'envoi, pas en HTML.
    return "".join(
        layout.paragraph(block.strip()).replace("\n", "<br/>")
        for block in text.strip().split("\n\n")
        if block.strip()
    )


def _wrap(locale, first_name, title, intro, rows, extra_html, note, cta=None):
    greeting = messages.text(locale, "greeting", name=first_name)
    body = layout.page(
        lang=locale,
        preheader=intro,
        blocks=[
            layout.heading(title),
            layout.paragraph(greeting),
            layout.paragraph(intro),
            layout.rows_table(rows),
            extra_html,
            layout.button(*cta) if cta else "",
        ],
        footer_html=layout.footer(
            note,
            messages.text(locale, "footer_no_reply"),
            (SETTINGS_URL, messages.text(locale, "unsubscribe")),
        ),
    )

    lines = [title, "", greeting, "", intro, ""]
    for row_title, meta, detail, amount, _tone in rows:
        lines.append(f"- {row_title} - {meta}" + (f" - {amount}" if amount else ""))
        if detail:
            lines.append(f"  {detail}")
    if cta:
        # Le bouton n'existe que dans la version riche : sans cette ligne, un
        # lecteur en texte seul n'avait aucun chemin vers l'application.
        lines += ["", f"{cta[1]} : {cta[0]}"]
    lines += [
        "",
        messages.text(locale, "unsubscribe") + " : " + SETTINGS_URL,
        note,
        messages.text(locale, "footer_no_reply"),
    ]
    return body, "\n".join(lines)


def _code_mail(locale: str, kind: str, code: str):
    subject_key, title_key, intro_key, ignore_key = CODE_MAILS[kind]
    subject = messages.text(locale, subject_key)
    title = messages.text(locale, title_key)
    intro = messages.text(locale, intro_key)
    expires = messages.text(locale, "code_expires")
    ignore = messages.text(locale, ignore_key)
    no_reply = messages.text(locale, "footer_no_reply")

    body = layout.page(
        lang=locale,
        preheader=intro,
        blocks=[
            layout.heading(title),
            layout.paragraph(intro),
            layout.code_panel(code),
            layout.paragraph(expires, layout.MUTED, 14),
        ],
        footer_html=layout.footer(ignore, no_reply),
    )
    plain = "\n".join([title, "", intro, "", code, "", expires, ignore, no_reply])
    return subject, body, plain


def _due_label(days_left: int, locale: str = "fr") -> str:
    if days_left <= 0:
        return messages.text(locale, "due_today")
    if days_left == 1:
        return messages.text(locale, "due_tomorrow")
    return messages.text(locale, "due_in_days", count=days_left)


def _late_label(days_left: int, locale: str = "fr") -> str:
    late = -days_left
    if late == 1:
        return messages.text(locale, "late_one_day")
    return messages.text(locale, "late_days", count=late)


class EmailSender:

    def __init__(self):
        resend.api_key = RESEND_API_KEY

    async def _send(self, params: Dict) -> Dict:
        return await asyncio.get_running_loop().run_in_executor(
            None,
            partial(resend.Emails.send, params),
        )

    def _params(self, to: str, subject: str, body: str, plain: str) -> Dict:
        return {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": body,
            "text": plain,
        }

    def _reminder_params(self, to: str, subject: str, body: str, plain: str) -> Dict:
        params = self._params(to, subject, body, plain)
        params["from"] = f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL_REMINDER}>"
        params["headers"] = {"List-Unsubscribe": f"<{SETTINGS_URL}>"}
        return params

    async def send_verification_email(self, to: str, code: str, locale: str = "fr") -> Dict:
        subject, body, plain = _code_mail(messages.pick(locale), "verify", code)
        return await self._send(self._params(to, subject, body, plain))

    async def send_reset_password_email(self, to: str, code: str, locale: str = "fr") -> Dict:
        subject, body, plain = _code_mail(messages.pick(locale), "reset", code)
        return await self._send(self._params(to, subject, body, plain))

    async def send_email_change_email(self, to: str, code: str, locale: str = "fr") -> Dict:
        subject, body, plain = _code_mail(messages.pick(locale), "change", code)
        return await self._send(self._params(to, subject, body, plain))

    async def send_admin_email(
        self, to: str, subject: str, body_text: str, locale: str = messages.DEFAULT_LOCALE
    ) -> Dict:
        locale = messages.pick(locale)
        body = layout.page(
            lang=locale,
            preheader=subject,
            blocks=[layout.heading(subject), _paragraphs(body_text)],
            footer_html=layout.footer(
                messages.text(locale, "admin_why"),
                messages.text(locale, "footer_no_reply"),
            ),
        )
        return await self._send(self._params(to, subject, body, body_text.strip()))

    async def send_reminder_email(
        self, to: str, *, first_name: str, items: list, currency: str, locale: str = "fr"
    ) -> Dict:
        locale = messages.pick(locale)
        count = len(items)
        subject = messages.text(
            locale,
            messages.plural(count, "notice_subject_one", "notice_subject_many"),
            count=count,
        )
        rows = [
            (
                str(item["title"]),
                f"{_due_label(item['days_left'], locale)} - {messages.day(item['due_date'], locale)}",
                "",
                messages.money(item["amount"], currency, locale),
                layout.MUTED,
            )
            for item in items
        ]
        body, plain = _wrap(
            locale,
            first_name,
            subject,
            messages.text(locale, messages.plural(count, "notice_intro_one", "notice_intro_many")),
            rows,
            "",
            messages.text(locale, "footer_why"),
            cta=(f"{FRONTEND_URL}/calendrier", messages.text(locale, "notice_cta")),
        )
        return await self._send(self._reminder_params(to, subject, body, plain))

    async def send_weekly_digest_email(
        self, to: str, *, first_name: str, items: list, currency: str, week_start, locale: str = "fr"
    ) -> Dict:
        locale = messages.pick(locale)
        count = len(items)
        total = sum(Decimal(str(item["amount"])) for item in items)
        subject = messages.text(
            locale, "weekly_subject", day=messages.day(week_start, locale)
        )
        rows = [
            (
                str(item["title"]),
                messages.day(item["due_date"], locale),
                "",
                messages.money(item["amount"], currency, locale),
                layout.MUTED,
            )
            for item in items
        ]
        # Le total sous le tableau : c'est la seule chose que ce courriel
        # apporte qu'un rappel a l'unite ne dit pas.
        extra = layout.paragraph(
            messages.text(
                locale, "weekly_total", amount=messages.money(total, currency, locale)
            )
        )
        body, plain = _wrap(
            locale,
            first_name,
            subject,
            messages.text(
                locale, messages.plural(count, "weekly_intro_one", "weekly_intro_many"), count=count
            ),
            rows,
            extra,
            messages.text(locale, "weekly_why"),
            cta=(f"{FRONTEND_URL}/calendrier", messages.text(locale, "weekly_cta")),
        )
        return await self._send(self._reminder_params(to, subject, body, plain))

    async def send_overdue_email(
        self, to: str, *, first_name: str, items: list, currency: str, locale: str = "fr"
    ) -> Dict:
        locale = messages.pick(locale)
        count = len(items)
        subject = messages.text(
            locale,
            messages.plural(count, "overdue_subject_one", "overdue_subject_many"),
            count=count,
        )
        rows = [
            (
                str(item["title"]),
                f"{_late_label(item['days_left'], locale)} - {messages.day(item['due_date'], locale)}",
                "",
                messages.money(item["amount"], currency, locale),
                layout.WARNING,
            )
            for item in items
        ]
        question = messages.text(
            locale, messages.plural(count, "overdue_question_one", "overdue_question_many")
        )
        extra = layout.paragraph(question)
        body, plain = _wrap(
            locale,
            first_name,
            subject,
            messages.text(locale, messages.plural(count, "overdue_intro_one", "overdue_intro_many")),
            rows,
            extra,
            messages.text(locale, "overdue_once"),
            cta=(f"{FRONTEND_URL}/calendrier", messages.text(locale, "overdue_cta")),
        )
        return await self._send(self._reminder_params(to, subject, body, plain))

    async def send_action_email(
        self, to: str, *, first_name: str, items: list, currency: str, locale: str = "fr"
    ) -> Dict:
        locale = messages.pick(locale)
        count = len(items)
        first = items[0]

        if count > 1:
            subject = messages.text(locale, "action_subject_many", count=count)
        elif first["reason"] == "trial":
            subject = messages.text(
                locale,
                "action_subject_trial",
                title=first["title"],
                when=_due_label(first["days_left"], locale),
            )
        else:
            subject = messages.text(
                locale,
                "action_subject_cancel",
                title=first["title"],
                date=messages.day(first["due_date"], locale),
            )

        rows = []
        for item in items:
            amount = messages.money(item["amount"], currency, locale)
            deadline = messages.day(item["deadline"], locale)
            due = messages.day(item["due_date"], locale)
            if item["reason"] == "trial":
                headline = messages.text(locale, "trial_headline", date=deadline)
                detail = messages.text(locale, "trial_detail", amount=amount, date=due)
            else:
                headline = messages.text(locale, "cancel_headline", date=due, amount=amount)
                detail = messages.text(locale, "cancel_detail", date=deadline)
            meta = f"{headline} - {_due_label(item['days_left'], locale)}"
            rows.append((str(item["title"]), meta, detail, "", layout.LINK))

        extra = ""
        body, plain = _wrap(
            locale,
            first_name,
            subject,
            messages.text(locale, messages.plural(count, "action_intro_one", "action_intro_many")),
            rows,
            extra,
            messages.text(locale, "action_once"),
            cta=(FRONTEND_URL, messages.text(locale, "open_app")),
        )
        return await self._send(self._reminder_params(to, subject, body, plain))
