import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


def send_email(subject: str, content: str, config: dict):
    api_key = os.getenv('SENDGRID_API_KEY') or config.get('SENDGRID_API_KEY')
    if not api_key:
        raise RuntimeError('SENDGRID_API_KEY not set in environment or config')

    message = Mail(
        from_email=config.get('FROM_EMAIL'),
        to_emails=config.get('TO_EMAIL'),
        subject=subject,
        html_content=content,
    )
    sg = SendGridAPIClient(api_key)
    resp = sg.send(message)
    return resp.status_code, resp.body
