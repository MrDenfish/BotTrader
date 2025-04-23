import os
import smtplib

""" This class handles the sending of alert messages, such as SMS or emails."""
#


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
        self.phone = os.getenv('PHONE')
        self.email = os.getenv('EMAIL')
        self.email_pass = os.getenv('E_MAILPASS')
        self.my_email = os.getenv('MY_EMAIL')
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
                'EMAIL': self.email,
                'E_MAILPASS': self.email_pass,
                'MY_EMAIL': self.my_email
            }.items() if not v]

            if missing:
                raise ValueError(f"Missing environment variables: {', '.join(missing)}")

    def callhome(self, subject, message, mode='sms'):
        try:
            if not self.email_alert_on:
                print(f"🔸 callhome() skipped — email alerts are disabled.")
                return

            to = self.phone + '@txt.att.net' if mode == 'sms' else self.my_email
            email_text = f'Subject: {subject}\n\n{message}'

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.email, self.email_pass)
                server.sendmail(self.email, to, email_text)

            self.logger(f"� Alert sent to {'SMS' if mode == 'sms' else 'Email'}: {to}")

        except Exception as e:
            self.logger(f"❌ Error sending alert: {e}", exc_info=True)
