import html

from core.config import LOGO_URL

BRAND = "OubliePas"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

BG = "#f1f5f9"
SURFACE = "#ffffff"
BORDER = "#dde6ee"
TEXT = "#0f2233"
MUTED = "#5c7285"
ACCENT = "#0f2233"
ACCENT_CONTRAST = "#ffffff"
ACCENT_SOFT = "#e3edf9"
LINK = "#1e63de"
WARNING = "#8a6520"


def escape(value) -> str:
    return html.escape(str(value))


def heading(value: str) -> str:
    return (
        f'<h1 style="margin:0 0 18px 0;font-family:{FONT};font-size:21px;line-height:1.3;'
        f'font-weight:600;letter-spacing:-0.02em;color:{TEXT};">{escape(value)}</h1>'
    )


def paragraph(value: str, color: str = TEXT, size: int = 15) -> str:
    return (
        f'<p style="margin:0 0 16px 0;font-family:{FONT};font-size:{size}px;line-height:1.6;'
        f'color:{color};">{escape(value)}</p>'
    )


def code_panel(value: str) -> str:
    # Le dernier caractere porte l'espacement de la lettre suivante : sans le
    # decalage a gauche, le code parait desaxe dans la boite.
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="margin:24px 0;"><tr>'
        f'<td align="center" style="background-color:{ACCENT_SOFT};border-radius:12px;'
        f'padding:20px 12px;font-family:{FONT};font-size:30px;font-weight:700;'
        f'letter-spacing:9px;text-indent:9px;color:{TEXT};">{escape(value)}</td>'
        "</tr></table>"
    )


def button(href: str, label: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="margin:4px 0 20px 0;"><tr><td align="center">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td align="center" style="background-color:{ACCENT};border-radius:10px;">'
        f'<a href="{escape(href)}" style="display:inline-block;padding:13px 26px;'
        f'font-family:{FONT};font-size:15px;font-weight:600;color:{ACCENT_CONTRAST};'
        f'text-decoration:none;">{escape(label)}</a>'
        "</td></tr></table></td></tr></table>"
    )


def row(title: str, meta: str, detail: str, amount: str, tone: str) -> str:
    extra = (
        f'<br/><span style="color:{MUTED};font-size:13px;line-height:1.5;">{escape(detail)}</span>'
        if detail
        else ""
    )
    right = (
        f'<td style="padding:14px 0;border-bottom:1px solid {BORDER};text-align:right;'
        f'vertical-align:top;white-space:nowrap;font-family:{FONT};font-size:15px;'
        f'font-weight:600;color:{TEXT};">{escape(amount)}</td>'
        if amount
        else ""
    )
    return (
        "<tr>"
        f'<td style="padding:14px 0;border-bottom:1px solid {BORDER};vertical-align:top;'
        f'font-family:{FONT};">'
        f'<strong style="font-size:15px;color:{TEXT};">{escape(title)}</strong><br/>'
        f'<span style="color:{tone};font-size:13px;line-height:1.5;">{escape(meta)}</span>{extra}'
        "</td>"
        f"{right}"
        "</tr>"
    )


def rows_table(rows) -> str:
    if not rows:
        return ""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="width:100%;border-collapse:collapse;margin:4px 0 24px 0;">'
        + "".join(row(*item) for item in rows)
        + "</table>"
    )


def footer(note: str, no_reply: str, link=None) -> str:
    tail = escape(no_reply)
    if link:
        href, label = link
        tail = (
            f'<a href="{escape(href)}" style="color:{LINK};text-decoration:none;">'
            f"{escape(label)}</a> &middot; " + tail
        )
    return (
        f'<p style="margin:0;font-family:{FONT};font-size:12px;line-height:1.6;'
        f'color:{MUTED};text-align:center;">{escape(note)}<br/>{tail}</p>'
    )


def _brand() -> str:
    mark = (
        f'<td style="padding-right:10px;"><img src="{escape(LOGO_URL)}" width="36" height="36"'
        f' alt="" style="display:block;width:36px;height:36px;border-radius:10px;" /></td>'
        if LOGO_URL
        else ""
    )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f"{mark}"
        f'<td style="font-family:{FONT};font-size:19px;font-weight:600;letter-spacing:-0.02em;'
        f'color:{TEXT};">{BRAND}</td>'
        "</tr></table>"
    )


def page(*, lang: str, preheader: str, blocks, footer_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="{escape(lang)}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="light" />
<meta name="supported-color-schemes" content="light" />
<title>{BRAND}</title>
</head>
<body style="margin:0;padding:0;background-color:{BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{BG};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;">
<tr><td align="center" style="padding-bottom:20px;">{_brand()}</td></tr>
<tr><td style="background-color:{SURFACE};border:1px solid {BORDER};border-radius:16px;padding:32px;">{"".join(blocks)}</td></tr>
<tr><td style="padding:20px 8px 0 8px;">{footer_html}</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
