import asyncio
import html
from functools import partial
from typing import Dict

import resend

from services.emailing import messages
from core.config import (
    FRONTEND_URL,
    RESEND_API_KEY,
    RESEND_FROM_EMAIL,
    RESEND_FROM_NAME,
)


def _plain_text_to_html_paragraphs(text: str) -> str:
    
    escaped = html.escape(text.strip())
    paragraphs = [p.strip() for p in escaped.split("\n\n") if p.strip()]
    return "".join(
        f'<p style="margin: 0 0 16px 0; line-height: 1.6;">{p.replace(chr(10), "<br/>")}</p>'
        for p in paragraphs
    )


SHELL = """
                <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px; color: #333;">
                    <h2 style="color: #0D9488; text-align: center; font-size: 22px; margin-top: 0;">OubliePas</h2>
                    <p>{greeting}</p>
                    <p>{intro}</p>
                    <table style="width: 100%; border-collapse: collapse; margin: 24px 0;">{rows}</table>
                    {extra}
                    <p style="color: #888; font-size: 12px; text-align: center; margin: 0;">
                        {footer}<br/>
                        <a href="{settings}" style="color: #0D9488;">{unsubscribe}</a> &middot; {no_reply}
                    </p>
                </div>
            """


def _button(href: str, label: str) -> str:
    return (
        '<div style="text-align: center; margin: 28px 0;">'
        f'<a href="{html.escape(href)}" style="background-color: #0D9488; color: #ffffff;'
        ' padding: 12px 28px; border-radius: 8px; text-decoration: none;'
        f' display: inline-block;">{html.escape(label)}</a></div>'
    )


def _row(title: str, meta: str, detail: str, amount: str, tone: str) -> str:
    extra = (
        f'<br/><span style="color: #888; font-size: 13px;">{html.escape(detail)}</span>'
        if detail
        else ""
    )
    right = (
        '<td style="padding: 12px 0; border-bottom: 1px solid #eee; text-align: right;'
        f' white-space: nowrap; color: #333;">{html.escape(amount)}</td>'
        if amount
        else ""
    )
    return f"""
                        <tr>
                            <td style="padding: 12px 0; border-bottom: 1px solid #eee;">
                                <strong style="color: #333;">{html.escape(title)}</strong><br/>
                                <span style="color: {tone}; font-size: 13px;">{html.escape(meta)}</span>{extra}
                            </td>
                            {right}
                        </tr>
            """


def _wrap(locale: str, first_name: str, intro: str, rows, extra_html: str, footer: str):
    settings = f"{FRONTEND_URL}/rappels"
    greeting = messages.text(locale, "greeting", name=first_name)
    body = SHELL.format(
        greeting=html.escape(greeting),
        intro=html.escape(intro),
        rows="".join(_row(*row) for row in rows),
        extra=extra_html,
        footer=html.escape(footer),
        settings=html.escape(settings),
        unsubscribe=html.escape(messages.text(locale, "unsubscribe")),
        no_reply=html.escape(messages.text(locale, "footer_no_reply")),
    )

    lines = [greeting, "", intro, ""]
    for title, meta, detail, amount, _tone in rows:
        lines.append(f"- {title} - {meta}" + (f" - {amount}" if amount else ""))
        if detail:
            lines.append(f"  {detail}")
    lines += [
        "",
        messages.text(locale, "unsubscribe") + " : " + settings,
        footer,
        messages.text(locale, "footer_no_reply"),
    ]
    return body, "\n".join(lines)


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

    async def send_verification_email(self, to: str, code: str) -> Dict:
        subject = "Email verification"
        html = f"""
                <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
                    <h2 style="color: #4db6ac; text-align: center; font-size: 24px;">Welcome to OubliePas</h2>
                    <p>Thank you for registering with OubliePas! Use the code below to verify your email address and complete your registration.</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <span style="background-color: #f5f5f5; color: #333; padding: 16px 32px; font-size: 32px; font-weight: bold; letter-spacing: 8px; border-radius: 8px; display: inline-block;">{code}</span>
                    </div>
                    <p>This code will expire in 15 minutes. Do not reply to this email.</p>
                    <p>If you did not create an account, please ignore this email.</p>
                    <p style="color: #888; font-size: 12px; text-align: center;">&copy; 2026 OubliePas. All rights reserved.</p>
                </div>
            """
        params = {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": html
        }
        return await self._send(params)

    async def send_reset_password_email(self, to: str, code: str) -> Dict:
        subject = "Password reset"
        html = f"""
                <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
                    <h2 style="color: #4db6ac; text-align: center; font-size: 24px;">Password Reset Request</h2>
                    <p>We received a request to reset your password. Use the code below to set a new password for your account.</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <span style="background-color: #f5f5f5; color: #333; padding: 16px 32px; font-size: 32px; font-weight: bold; letter-spacing: 8px; border-radius: 8px; display: inline-block;">{code}</span>
                    </div>
                    <p>This code will expire in 15 minutes. Do not reply to this email.</p>
                    <p>If you did not request a password reset, please ignore this email.</p>
                    <p>Thank you,<br/>The OubliePas Team</p>
                </div>
            """
        params = {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": html
        }
        return await self._send(params)

    async def send_admin_email(self, to: str, subject: str, body_text: str) -> Dict:
        body_html = _plain_text_to_html_paragraphs(body_text)
        html_template = f"""
                <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px; color: #333;">
                    <h2 style="color: #0D9488; text-align: center; font-size: 22px; margin-top: 0;">OubliePas</h2>
                    <div style="margin: 24px 0;">{body_html}</div>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;" />
                    <p style="color: #888; font-size: 12px; text-align: center; margin: 0;">
                        Ce message vous est envoyé par l'équipe OubliePas.<br/>
                        Ne pas répondre directement à cet email.
                    </p>
                </div>
            """
        params = {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": html_template,
        }
        return await self._send(params)

    def _reminder_params(self, to: str, subject: str, body: str, plain: str) -> Dict:
        return {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": body,
            "text": plain,
            "headers": {"List-Unsubscribe": f"<{FRONTEND_URL}/rappels>"},
        }

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
                "#888",
            )
            for item in items
        ]
        body, plain = _wrap(
            locale,
            first_name,
            messages.text(locale, messages.plural(count, "notice_intro_one", "notice_intro_many")),
            rows,
            "",
            messages.text(locale, "footer_why"),
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
                "#b45309",
            )
            for item in items
        ]
        question = messages.text(
            locale, messages.plural(count, "overdue_question_one", "overdue_question_many")
        )
        extra = f"<p>{html.escape(question)}</p>" + _button(
            f"{FRONTEND_URL}/calendrier", messages.text(locale, "overdue_cta")
        )
        body, plain = _wrap(
            locale,
            first_name,
            messages.text(locale, messages.plural(count, "overdue_intro_one", "overdue_intro_many")),
            rows,
            extra,
            messages.text(locale, "overdue_once"),
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
            rows.append((str(item["title"]), meta, detail, "", "#0D9488"))

        extra = _button(FRONTEND_URL, messages.text(locale, "open_app"))
        body, plain = _wrap(
            locale,
            first_name,
            messages.text(locale, messages.plural(count, "action_intro_one", "action_intro_many")),
            rows,
            extra,
            messages.text(locale, "action_once"),
        )
        return await self._send(self._reminder_params(to, subject, body, plain))

    async def send_email_change_email(self, to: str, code: str) -> Dict:
        subject = "Email change verification"
        html = f"""
                <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
                    <h2 style="color: #4db6ac; text-align: center; font-size: 24px;">Email Change Request</h2>
                    <p>We received a request to change the email address on your OubliePas account. Use the code below to confirm this change.</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <span style="background-color: #f5f5f5; color: #333; padding: 16px 32px; font-size: 32px; font-weight: bold; letter-spacing: 8px; border-radius: 8px; display: inline-block;">{code}</span>
                    </div>
                    <p>This code will expire in 15 minutes. Do not reply to this email.</p>
                    <p>If you did not request this change, please ignore this email.</p>
                    <p>Thank you,<br/>The OubliePas Team</p>
                </div>
            """
        params = {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": html
        }
        return await self._send(params)