from datetime import date
from decimal import Decimal

DEFAULT_LOCALE = "fr"

MONTHS = {
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin",
           "juill.", "août", "sept.", "oct.", "nov.", "déc."),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}

GROUP_SPACE = " "


def pick(locale: str | None) -> str:
    return locale if locale in MESSAGES else DEFAULT_LOCALE


def money(amount: Decimal, currency: str, locale: str) -> str:
    grouped = f"{Decimal(amount):,.2f}"
    if locale == "fr":
        grouped = grouped.translate(str.maketrans({",": GROUP_SPACE, ".": ","}))
        return f"{grouped}{GROUP_SPACE}{currency}"
    return f"{grouped} {currency}"


def day(value: date, locale: str) -> str:
    month = MONTHS[locale][value.month - 1]
    if locale == "fr":
        return f"{value.day} {month} {value.year}"
    return f"{month} {value.day}, {value.year}"


def plural(count: int, one: str, other: str) -> str:
    return one if count == 1 else other


def text(locale: str, key: str, **values) -> str:
    template = MESSAGES[pick(locale)][key]
    return template.format(**values) if values else template


MESSAGES = {
    "fr": {
        "greeting": "Bonjour {name},",
        "footer_no_reply": "Ne réponds pas directement à ce courriel.",
        "footer_why": "Tu reçois ce message parce que les rappels sont activés sur ton compte.",
        "admin_why": "Ce message t'est envoyé par l'équipe OubliePas.",
        "unsubscribe": "Gérer mes rappels",
        "open_app": "Ouvrir OubliePas",

        "verify_subject": "Vérification de ton adresse",
        "verify_title": "Bienvenue sur OubliePas",
        "verify_intro": "Utilise ce code pour confirmer ton adresse et terminer ton inscription.",
        "code_expires": "Ce code expire dans 15 minutes.",
        "verify_ignore": "Si tu n'as pas créé de compte, ignore ce message.",

        "reset_subject": "Réinitialisation du mot de passe",
        "reset_title": "Nouveau mot de passe",
        "reset_intro": "Utilise ce code pour choisir un nouveau mot de passe.",
        "reset_ignore": "Si tu n'as rien demandé, ignore ce message.",

        "change_subject": "Confirmation du changement d'adresse",
        "change_title": "Changement d'adresse",
        "change_intro": "Utilise ce code pour confirmer ta nouvelle adresse.",
        "change_ignore": "Si tu n'as rien demandé, ignore ce message.",

        "notice_subject_one": "1 échéance à venir",
        "notice_subject_many": "{count} échéances à venir",
        "notice_intro_one": "Voici ta prochaine échéance :",
        "notice_intro_many": "Voici tes prochaines échéances :",
        "due_today": "aujourd'hui",
        "due_tomorrow": "demain",
        "due_in_days": "dans {count} jours",

        "overdue_subject_one": "1 échéance en retard",
        "overdue_subject_many": "{count} échéances en retard",
        "overdue_intro_one": "Cette échéance est passée et reste en attente :",
        "overdue_intro_many": "Ces échéances sont passées et restent en attente :",
        "overdue_question_one": "Déjà payée ? Pas ce mois-ci ? Ou simplement oubliée ?",
        "overdue_question_many": "Déjà payées ? Pas ce mois-ci ? Ou simplement oubliées ?",
        "overdue_cta": "Mettre à jour",
        "late_one_day": "en retard d'un jour",
        "late_days": "en retard de {count} jours",
        "overdue_once": "Ce rappel de retard n'est envoyé qu'une seule fois par échéance.",

        "action_subject_many": "{count} décisions à prendre",
        "action_subject_trial": "Ton essai {title} se termine {when}",
        "action_subject_cancel": "{title} se renouvelle le {date}",
        "action_intro_one": "Il y a une décision à prendre, avant qu'il ne soit trop tard :",
        "action_intro_many": "Voici les échéances qui demandent une décision, avant qu'il ne soit trop tard :",
        "trial_headline": "Essai gratuit jusqu'au {date}",
        "trial_detail": "Sans action de ta part, le prélèvement de {amount} commence le {date}.",
        "cancel_headline": "Renouvellement le {date} pour {amount}",
        "cancel_detail": "Pour annuler, tu dois aviser avant le {date}.",
        "action_once": "Ce rappel n'est envoyé qu'une seule fois par échéance.",
    },
    "en": {
        "greeting": "Hi {name},",
        "footer_no_reply": "Please do not reply directly to this email.",
        "footer_why": "You are getting this because reminders are switched on for your account.",
        "admin_why": "This message was sent to you by the OubliePas team.",
        "unsubscribe": "Manage my reminders",
        "open_app": "Open OubliePas",

        "verify_subject": "Verify your email address",
        "verify_title": "Welcome to OubliePas",
        "verify_intro": "Use this code to confirm your address and finish signing up.",
        "code_expires": "This code expires in 15 minutes.",
        "verify_ignore": "If you did not create an account, please ignore this message.",

        "reset_subject": "Password reset",
        "reset_title": "New password",
        "reset_intro": "Use this code to choose a new password.",
        "reset_ignore": "If you did not ask for this, please ignore this message.",

        "change_subject": "Confirm your new email address",
        "change_title": "Email change",
        "change_intro": "Use this code to confirm your new address.",
        "change_ignore": "If you did not ask for this, please ignore this message.",

        "notice_subject_one": "1 payment coming up",
        "notice_subject_many": "{count} payments coming up",
        "notice_intro_one": "Here is your next payment:",
        "notice_intro_many": "Here are your next payments:",
        "due_today": "today",
        "due_tomorrow": "tomorrow",
        "due_in_days": "in {count} days",

        "overdue_subject_one": "1 payment is late",
        "overdue_subject_many": "{count} payments are late",
        "overdue_intro_one": "This payment is past due and still pending:",
        "overdue_intro_many": "These payments are past due and still pending:",
        "overdue_question_one": "Already paid? Not this month? Or simply forgotten?",
        "overdue_question_many": "Already paid? Not this month? Or simply forgotten?",
        "overdue_cta": "Update it",
        "late_one_day": "one day late",
        "late_days": "{count} days late",
        "overdue_once": "This late notice is sent only once per payment.",

        "action_subject_many": "{count} decisions to make",
        "action_subject_trial": "Your {title} trial ends {when}",
        "action_subject_cancel": "{title} renews on {date}",
        "action_intro_one": "There is a decision to make, before it is too late:",
        "action_intro_many": "These payments need a decision from you, before it is too late:",
        "trial_headline": "Free trial until {date}",
        "trial_detail": "If you do nothing, the {amount} charge starts on {date}.",
        "cancel_headline": "Renews on {date} for {amount}",
        "cancel_detail": "To cancel, you must give notice before {date}.",
        "action_once": "This reminder is sent only once per payment.",
    },
}
