import os
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except ImportError:  # pragma: no cover
    SendGridAPIClient = None
    Mail = None


PLACEHOLDER_VALUES = {
    'SENDGRID_API_KEY': {'REPLACE_WITH_YOUR_SENDGRID_API_KEY', 'YOUR_SENDGRID_API_KEY'},
    'FROM_EMAIL': {'alerts@example.com', 'example@example.com', 'you@example.com'},
    'TO_EMAIL': {'you@example.com', 'example@example.com'},
}


def _read_value(config: dict, *keys, default=''):
    for key in keys:
        value = os.getenv(key)
        if value is not None:
            return value
        value = config.get(key)
        if value is not None:
            return value
    return default


def _is_placeholder(value: str, placeholders):
    if value is None:
        return True
    value = str(value).strip()
    if not value:
        return True
    return value.lower() in {p.lower() for p in placeholders}


def _validate_email_address(value: str, name: str):
    if not value or '\r' in value or '\n' in value:
        raise RuntimeError(f'{name} is missing or invalid.')
    _, address = parseaddr(value)
    if not address or '@' not in address:
        raise RuntimeError(f'{name} is missing or invalid.')


def validate_sendgrid_config(config: dict):
    api_key = str(_read_value(config, 'SENDGRID_API_KEY', default='')).strip()
    from_email = str(_read_value(config, 'FROM_EMAIL', default='')).strip()
    to_email = str(_read_value(config, 'TO_EMAIL', default='')).strip()

    if not api_key or _is_placeholder(api_key, PLACEHOLDER_VALUES['SENDGRID_API_KEY']):
        raise RuntimeError(
            'SENDGRID_API_KEY is missing or still set to a placeholder value. '
            'Set a real SendGrid API key in the environment or config.yaml.'
        )

    if not from_email or _is_placeholder(from_email, PLACEHOLDER_VALUES['FROM_EMAIL']):
        raise RuntimeError(
            'FROM_EMAIL is missing or still set to a placeholder value. '
            'Use a verified SendGrid sender email.'
        )

    if not to_email or _is_placeholder(to_email, PLACEHOLDER_VALUES['TO_EMAIL']):
        raise RuntimeError(
            'TO_EMAIL is missing or still set to a placeholder value. '
            'Use the recipient email for alerts.'
        )

    return api_key, from_email, to_email


def get_smtp_config(config: dict):
    smtp_host = str(_read_value(config, 'SMTP_HOST', default='')).strip()
    smtp_port = int(str(_read_value(config, 'SMTP_PORT', default='587')).strip() or 587)
    smtp_username = str(_read_value(config, 'SMTP_USERNAME', default='')).strip()
    smtp_password = str(_read_value(config, 'SMTP_PASSWORD', default='')).strip()
    from_email = str(_read_value(config, 'FROM_EMAIL', default='')).strip()
    to_email = str(_read_value(config, 'TO_EMAIL', default='')).strip()
    use_tls = str(_read_value(config, 'SMTP_USE_TLS', default='true')).strip().lower() not in {'0', 'false', 'no'}

    if not smtp_host or not smtp_username or not smtp_password:
        return None
    if smtp_port == 587 and not use_tls:
        raise RuntimeError('SMTP_USE_TLS must remain enabled when using SMTP port 587.')
    _validate_email_address(smtp_username, 'SMTP_USERNAME')
    _validate_email_address(from_email or smtp_username, 'FROM_EMAIL')
    _validate_email_address(to_email, 'TO_EMAIL')
    return {
        'host': smtp_host,
        'port': smtp_port,
        'username': smtp_username,
        'password': smtp_password,
        'from_email': from_email,
        'to_email': to_email,
        'use_tls': use_tls,
    }


def send_email(subject: str, content: str, config: dict):
    email_provider = str(_read_value(config, 'EMAIL_PROVIDER', default='')).strip().lower()

    try:
        api_key = _read_value(config, 'SENDGRID_API_KEY', default='')
        if (email_provider in {'', 'sendgrid'} or api_key) and SendGridAPIClient is not None:
            api_key = str(api_key).strip()
            if api_key and not _is_placeholder(api_key, PLACEHOLDER_VALUES['SENDGRID_API_KEY']):
                api_key, from_email, to_email = validate_sendgrid_config(config)
                message = Mail(
                    from_email=from_email,
                    to_emails=to_email,
                    subject=subject,
                    html_content=content,
                )
                sg = SendGridAPIClient(api_key)
                resp = sg.send(message)
                return resp.status_code, resp.body
    except RuntimeError:
        pass

    smtp_cfg = get_smtp_config(config)
    if smtp_cfg:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = smtp_cfg['from_email'] or smtp_cfg['username']
        msg['To'] = smtp_cfg['to_email']
        msg.set_content(content)
        msg.add_alternative(content, subtype='html')

        with smtplib.SMTP(smtp_cfg['host'], smtp_cfg['port'], timeout=30) as server:
            if smtp_cfg['use_tls']:
                server.starttls()
            server.login(smtp_cfg['username'], smtp_cfg['password'])
            server.send_message(msg)
        return 200, b'ok'

    raise RuntimeError(
        'No valid email backend configured. Set SENDGRID_API_KEY or SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD.'
    )
