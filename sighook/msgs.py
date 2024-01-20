
import os
import smtplib


from dotenv import load_dotenv


class TradeBotComs:
    load_dotenv()
    phone = os.getenv('PHONE')
    email = os.getenv('EMAIL')
    e_mailpass = os.getenv('E_MAILPASS')
    my_email = os.getenv('MY_EMAIL')
    balances = {}  # Class attribute to store balances

    def __init__(self, logmanager):
        self.log_manager = logmanager

    def callhome(self, subject, message):
        try:
            #  logger.info('Sending SMS alert')
            to = f'{self.phone}@txt.att.net'  # Format the phone number as an Email-to-SMS gateway address
            email_text = f'Subject: {subject}\n\n{message}'
            server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
            server.login(self.email, self.e_mailpass)
            server.sendmail(self.my_email, to, email_text)
            server.quit()
        except Exception as e:
            print(f'Error sending SMS alert: {e}')
            self.log_manager.signal_generator_logger.error(f'Error sending SMS alert: {e}')
