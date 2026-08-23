import asyncio
import html
from functools import partial
from typing import Dict

import resend
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


def _late_label(days_left: int) -> str:
    late = -days_left
    if late == 1:
        return "en retard d'un jour"
    return f"en retard de {late} jours"


def _action_lines(item: dict, currency: str) -> tuple[str, str]:
    amount = f"{item['amount']:.2f} {html.escape(currency)}"
    deadline = item["deadline"].strftime("%d/%m/%Y")
    due = item["due_date"].strftime("%d/%m/%Y")

    if item["reason"] == "trial":
        return (
            f"Essai gratuit jusqu'au {deadline}",
            f"Sans action de ta part, le prélèvement de {amount} commence le {due}.",
        )
    return (
        f"Renouvellement le {due} pour {amount}",
        f"Pour annuler, tu dois aviser avant le {deadline}.",
    )


def _due_label(days_left: int) -> str:
    if days_left <= 0:
        return "aujourd'hui"
    if days_left == 1:
        return "demain"
    return f"dans {days_left} jours"


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

    async def send_reminder_email(
        self, to: str, *, first_name: str, items: list, currency: str
    ) -> Dict:
        count = len(items)
        subject = f"{count} échéance{'s' if count > 1 else ''} à venir"

        rows = "".join(
            f"""
                        <tr>
                            <td style="padding: 12px 0; border-bottom: 1px solid #eee;">
                                <strong style="color: #333;">{html.escape(str(item["title"]))}</strong><br/>
                                <span style="color: #888; font-size: 13px;">{_due_label(item["days_left"])} &middot; {item["due_date"].strftime("%d/%m/%Y")}</span>
                            </td>
                            <td style="padding: 12px 0; border-bottom: 1px solid #eee; text-align: right; white-space: nowrap; color: #333;">
                                {item["amount"]:.2f} {html.escape(currency)}
                            </td>
                        </tr>
            """
            for item in items
        )

        body = f"""
                <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px; color: #333;">
                    <h2 style="color: #0D9488; text-align: center; font-size: 22px; margin-top: 0;">OubliePas</h2>
                    <p>Bonjour {html.escape(first_name)},</p>
                    <p>Voici {'vos' if count > 1 else 'votre'} prochaine{'s' if count > 1 else ''} échéance{'s' if count > 1 else ''} :</p>
                    <table style="width: 100%; border-collapse: collapse; margin: 24px 0;">{rows}</table>
                    <p style="color: #888; font-size: 12px; text-align: center; margin: 0;">
                        Vous recevez ce message parce que les rappels sont actifs sur ces engagements.<br/>
                        Ne pas répondre directement à cet email.
                    </p>
                </div>
            """
        params = {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": body,
        }
        return await self._send(params)

    async def send_overdue_email(
        self, to: str, *, first_name: str, items: list, currency: str
    ) -> Dict:
        count = len(items)
        plural = "s" if count > 1 else ""
        subject = f"{count} échéance{plural} en retard"

        rows = "".join(
            f"""
                        <tr>
                            <td style="padding: 12px 0; border-bottom: 1px solid #eee;">
                                <strong style="color: #333;">{html.escape(str(item["title"]))}</strong><br/>
                                <span style="color: #b45309; font-size: 13px;">{_late_label(item["days_left"])} &middot; {item["due_date"].strftime("%d/%m/%Y")}</span>
                            </td>
                            <td style="padding: 12px 0; border-bottom: 1px solid #eee; text-align: right; white-space: nowrap; color: #333;">
                                {item["amount"]:.2f} {html.escape(currency)}
                            </td>
                        </tr>
            """
            for item in items
        )

        body = f"""
                <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px; color: #333;">
                    <h2 style="color: #0D9488; text-align: center; font-size: 22px; margin-top: 0;">OubliePas</h2>
                    <p>Bonjour {html.escape(first_name)},</p>
                    <p>{"Ces échéances sont passées" if count > 1 else "Cette échéance est passée"} et {"restent" if count > 1 else "reste"} en attente :</p>
                    <table style="width: 100%; border-collapse: collapse; margin: 24px 0;">{rows}</table>
                    <p>Déjà payé{plural} ? Pas {"ces mois-ci" if count > 1 else "ce mois-ci"} ? Ou simplement oublié{plural} ? Mettez {"-les" if count > 1 else "-la"} à jour en un geste.</p>
                    <div style="text-align: center; margin: 28px 0;">
                        <a href="{html.escape(FRONTEND_URL)}/calendrier" style="background-color: #0D9488; color: #ffffff; padding: 12px 28px; border-radius: 8px; text-decoration: none; display: inline-block;">Mettre à jour</a>
                    </div>
                    <p style="color: #888; font-size: 12px; text-align: center; margin: 0;">
                        Ce rappel de retard n'est envoyé qu'une seule fois par échéance.<br/>
                        Ne pas répondre directement à cet email.
                    </p>
                </div>
            """
        params = {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": body,
        }
        return await self._send(params)

    async def send_action_email(
        self, to: str, *, first_name: str, items: list, currency: str
    ) -> Dict:
        count = len(items)
        first = items[0]

        if count > 1:
            subject = f"{count} décisions à prendre"
        elif first["reason"] == "trial":
            subject = f"Ton essai {first['title']} se termine {_due_label(first['days_left'])}"
        else:
            subject = f"{first['title']} se renouvelle le {first['due_date'].strftime('%d/%m/%Y')}"

        rows = ""
        for item in items:
            headline, detail = _action_lines(item, currency)
            rows += f"""
                        <tr>
                            <td style="padding: 14px 0; border-bottom: 1px solid #eee;">
                                <strong style="color: #333;">{html.escape(str(item["title"]))}</strong>
                                <span style="color: #0D9488; font-size: 13px; font-weight: 600;">&middot; {_due_label(item["days_left"])}</span><br/>
                                <span style="color: #555; font-size: 13px;">{headline}</span><br/>
                                <span style="color: #888; font-size: 13px;">{detail}</span>
                            </td>
                        </tr>
            """

        body = f"""
                <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px; color: #333;">
                    <h2 style="color: #0D9488; text-align: center; font-size: 22px; margin-top: 0;">OubliePas</h2>
                    <p>Bonjour {html.escape(first_name)},</p>
                    <p>{"Voici les échéances qui demandent une décision de ta part" if count > 1 else "Il y a une décision à prendre"}, avant qu'il ne soit trop tard :</p>
                    <table style="width: 100%; border-collapse: collapse; margin: 24px 0;">{rows}</table>
                    <div style="text-align: center; margin: 28px 0;">
                        <a href="{html.escape(FRONTEND_URL)}" style="background-color: #0D9488; color: #ffffff; padding: 12px 28px; border-radius: 8px; text-decoration: none; display: inline-block;">Ouvrir OubliePas</a>
                    </div>
                    <p style="color: #888; font-size: 12px; text-align: center; margin: 0;">
                        Ce rappel n'est envoyé qu'une seule fois par échéance.<br/>
                        Ne pas répondre directement à cet email.
                    </p>
                </div>
            """
        params = {
            "from": f"{RESEND_FROM_NAME} <{RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": body,
        }
        return await self._send(params)

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