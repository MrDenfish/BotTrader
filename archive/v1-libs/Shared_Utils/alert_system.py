import os
import smtplib
from typing import Optional




""" This class handles the sending of alert messages, such as SMS or emails."""
#
def _get(*names):
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None

class AlertSystem:
    _instance = None

    @classmethod
    def get_instance(cls, logger_manager):
        """
        Singleton method to ensure only one instance of AlertSystem exists.
        """
        if cls._instance is None:
            cls._instance = cls(logger_manager)
        return cls._instance
    def __init__(self, logger_manager):
        self.logger = logger_manager.loggers['shared_logger']
        self.phone = _get('PHONE', 'ACCOUNT_PHONE', 'ALERT_PHONE')
        self.email_from = os.getenv('REPORT_SENDER')
        self.email_pass = os.getenv('SMTP_PASSWORD')
        self.email_to = os.getenv('REPORT_RECIPIENTS')
        self.email_alert_on = os.getenv('EMAIL_ALERTS', 'true').lower() == 'true'

        if self.email_alert_on:
            self.validate_env()
            self.logger.info("🔹 Email alerts are enabled.")
        else:
            self.logger.info("🔸Email alerts are DISABLED via EMAIL_ALERTS=False.")

    def validate_env(self):
        if self.email_alert_on:
            missing = [k for k, v in {
                'PHONE': self.phone,
                'EMAIL_FROM': self.email_from,
                'SMTP_PASSWORD': self.email_pass,
                'EMAIL_TO': self.email_to
            }.items() if not v]

            if missing:
                raise ValueError(f"Missing environment variables: {', '.join(missing)}")

    def callhome(self, subject, message, mode='sms'):
        try:
            if not self.email_alert_on:
                print(f"🔸 callhome() skipped — email alerts are disabled.")
                return

            to = self.phone + '@txt.att.net' if mode == 'sms' else self.email_to
            email_text = f'Subject: {subject}\n\n{message}'

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.email_from, self.email_pass)
                server.sendmail(self.email_from, to, email_text)

            self.logger(f"� Alert sent to {'SMS' if mode == 'sms' else 'Email'}: {to}")

        except Exception as e:
            self.logger(f"❌ Error sending alert: {e}", exc_info=True)

    def summarize_user_snapshot(self, data: dict) -> Optional[str]:
        try:
            events = data.get("events", [])
            for event in events:
                if event.get("type") != "snapshot":
                    continue
                orders = event.get("orders", [])
                if not orders:
                    return None
                summaries = []
                for order in orders:
                    summary = (
                        f"📬 User Snapshot:\n"
                        f"- Symbol: {order.get('product_id')}\n"
                        f"- Side: {order.get('order_side')}\n"
                        f"- Type: {order.get('order_type')}\n"
                        f"- Status: {order.get('status')}\n"
                        f"- Limit Price: {order.get('limit_price')}\n"
                        f"- Remaining Qty: {order.get('leaves_quantity')}\n"
                        f"- Order ID: {order.get('order_id')}\n"
                        f"- Created At: {order.get('creation_time')}\n"
                    )
                    summaries.append(summary)
                return "\n".join(summaries)
        except Exception as e:
            self.logger.error(f"❌ Error summarizing user snapshot: {e}", exc_info=True)
            return None
