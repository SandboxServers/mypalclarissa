"""
Email monitoring module for Clara Discord Bot.

Provides:
- Background task that checks for new emails every 60 seconds
- Notifies specified user via DM when new mail arrives
- check_email tool for on-demand email checking
"""

import asyncio
import imaplib
import email
from email.header import decode_header
from datetime import datetime, UTC
from dataclasses import dataclass
import os

# Email configuration - loaded from environment or hardcoded for now
EMAIL_ADDRESS = os.environ.get("CLARA_EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("CLARA_EMAIL_PASSWORD")
IMAP_SERVER = os.getenv("CLARA_IMAP_SERVER", "imap.titan.email")
IMAP_PORT = int(os.getenv("CLARA_IMAP_PORT", "993"))

# Discord user ID to notify (Joshua's Discord ID)
NOTIFY_USER_ID = int(os.getenv("CLARA_EMAIL_NOTIFY_USER", "271274659385835521"))

# Check interval in seconds
CHECK_INTERVAL = int(os.getenv("CLARA_EMAIL_CHECK_INTERVAL", "60"))


@dataclass
class EmailInfo:
    """Represents an email message."""
    uid: str
    from_addr: str
    subject: str
    date: str
    preview: str = ""


class EmailMonitor:
    """Monitors an IMAP inbox for new messages."""
    
    def __init__(self):
        self.seen_uids: set[str] = set()
        self.initialized = False
        self.last_check: datetime | None = None
        self.last_error: str | None = None
    
    def _connect(self) -> imaplib.IMAP4_SSL:
        """Create IMAP connection."""
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        return mail
    
    def _decode_header_value(self, value: str) -> str:
        """Decode email header (handles encoded words)."""
        if not value:
            return ""
        decoded_parts = decode_header(value)
        result = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or 'utf-8', errors='replace'))
            else:
                result.append(part)
        return ''.join(result)
    
    def _get_email_preview(self, msg) -> str:
        """Extract a text preview from email body."""
        preview = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            preview = payload.decode('utf-8', errors='replace')[:200]
                            break
                    except Exception:
                        pass
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    preview = payload.decode('utf-8', errors='replace')[:200]
            except Exception:
                pass
        return preview.strip().replace('\n', ' ')[:150] + "..." if len(preview) > 150 else preview.strip()
    
    def check_emails(self, unseen_only: bool = True) -> tuple[list[EmailInfo], str | None]:
        """
        Check inbox for emails.
        
        Args:
            unseen_only: If True, only return unseen messages
            
        Returns:
            tuple: (list of EmailInfo, error message or None)
        """
        try:
            mail = self._connect()
            mail.select("INBOX")
            
            # Search for messages
            search_criteria = "UNSEEN" if unseen_only else "ALL"
            status, data = mail.search(None, search_criteria)
            
            if status != "OK":
                mail.logout()
                return [], f"Search failed: {status}"
            
            emails = []
            message_nums = data[0].split()
            
            for num in message_nums[-10:]:  # Limit to last 10
                status, msg_data = mail.fetch(num, "(UID RFC822.HEADER)")
                if status != "OK":
                    continue
                    
                # Get UID
                uid_match = None
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        uid_part = response_part[0].decode()
                        if "UID" in uid_part:
                            import re
                            match = re.search(r'UID (\d+)', uid_part)
                            if match:
                                uid_match = match.group(1)
                        
                        # Parse headers
                        msg = email.message_from_bytes(response_part[1])
                        from_addr = self._decode_header_value(msg.get("From", ""))
                        subject = self._decode_header_value(msg.get("Subject", "(No Subject)"))
                        date = msg.get("Date", "")
                        
                        emails.append(EmailInfo(
                            uid=uid_match or str(num.decode()),
                            from_addr=from_addr,
                            subject=subject,
                            date=date
                        ))
            
            mail.logout()
            self.last_check = datetime.now(UTC)
            self.last_error = None
            return emails, None
            
        except Exception as e:
            self.last_error = str(e)
            return [], str(e)
    
    def get_new_emails(self) -> tuple[list[EmailInfo], str | None]:
        """
        Check for new emails since last check.
        
        Returns emails that haven't been seen before.
        """
        emails, error = self.check_emails(unseen_only=True)
        
        if error:
            return [], error
        
        if not self.initialized:
            # First run - just record what's there, don't notify
            self.seen_uids = {e.uid for e in emails}
            self.initialized = True
            return [], None
        
        # Find new emails
        new_emails = [e for e in emails if e.uid not in self.seen_uids]
        
        # Update seen set
        self.seen_uids.update(e.uid for e in emails)
        
        return new_emails, None
    
    def get_all_emails(self, limit: int = 10) -> tuple[list[EmailInfo], str | None]:
        """Get all recent emails (for on-demand check)."""
        try:
            mail = self._connect()
            mail.select("INBOX")
            
            status, data = mail.search(None, "ALL")
            if status != "OK":
                mail.logout()
                return [], f"Search failed: {status}"
            
            emails = []
            message_nums = data[0].split()
            
            # Get last N messages
            for num in message_nums[-limit:]:
                status, msg_data = mail.fetch(num, "(UID FLAGS RFC822.HEADER)")
                if status != "OK":
                    continue
                
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        # Check flags for seen status
                        flags_part = response_part[0].decode()
                        is_seen = "\\Seen" in flags_part
                        
                        # Get UID
                        import re
                        uid_match = re.search(r'UID (\d+)', flags_part)
                        uid = uid_match.group(1) if uid_match else str(num.decode())
                        
                        # Parse headers
                        msg = email.message_from_bytes(response_part[1])
                        from_addr = self._decode_header_value(msg.get("From", ""))
                        subject = self._decode_header_value(msg.get("Subject", "(No Subject)"))
                        date = msg.get("Date", "")
                        
                        emails.append(EmailInfo(
                            uid=uid,
                            from_addr=from_addr,
                            subject=subject,
                            date=date,
                            preview="[unread]" if not is_seen else ""
                        ))
            
            mail.logout()
            self.last_check = datetime.now(UTC)
            return emails, None
            
        except Exception as e:
            return [], str(e)


# Global monitor instance
_email_monitor: EmailMonitor | None = None


def get_email_monitor() -> EmailMonitor:
    """Get or create the email monitor instance."""
    global _email_monitor
    if _email_monitor is None:
        _email_monitor = EmailMonitor()
    return _email_monitor


# Tool definition for check_email
EMAIL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_email",
            "description": "Check Clara's email inbox (clara@jorsh.net). Returns recent emails with sender, subject, and date. Use this when asked about email or to check for new messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unread_only": {
                        "type": "boolean",
                        "description": "If true, only show unread emails. Default is false (show all recent)."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default: 10, max: 25)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email from Clara's email address (clara@jorsh.net).",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line"
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body text"
                    },
                    "reply_to_uid": {
                        "type": "string",
                        "description": "Optional: UID of email to reply to (for threading)"
                    }
                },
                "required": ["to", "subject", "body"]
            }
        }
    }
]


async def handle_email_tool(tool_name: str, arguments: dict) -> str:
    """Handle email tool calls."""
    monitor = get_email_monitor()
    
    if tool_name == "check_email":
        unread_only = arguments.get("unread_only", False)
        limit = min(arguments.get("limit", 10), 25)
        
        if unread_only:
            emails, error = monitor.check_emails(unseen_only=True)
        else:
            emails, error = monitor.get_all_emails(limit=limit)
        
        if error:
            return f"Error checking email: {error}"
        
        if not emails:
            return "No emails found." if not unread_only else "No unread emails."
        
        # Format results
        lines = [f"Found {len(emails)} email(s):\n"]
        for i, e in enumerate(emails, 1):
            status = " [UNREAD]" if e.preview == "[unread]" else ""
            lines.append(f"{i}. **From:** {e.from_addr}")
            lines.append(f"   **Subject:** {e.subject}{status}")
            lines.append(f"   **Date:** {e.date}")
            lines.append("")
        
        return "\n".join(lines)
    
    elif tool_name == "send_email":
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        to_addr = arguments.get("to", "")
        subject = arguments.get("subject", "")
        body = arguments.get("body", "")
        
        if not to_addr or not subject or not body:
            return "Error: to, subject, and body are all required"
        
        try:
            # SMTP settings for Titan
            smtp_server = os.getenv("CLARA_SMTP_SERVER", "smtp.titan.email")
            smtp_port = int(os.getenv("CLARA_SMTP_PORT", "465"))
            
            msg = MIMEMultipart()
            msg["From"] = EMAIL_ADDRESS
            msg["To"] = to_addr
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))
            
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.send_message(msg)
            
            return f"Email sent successfully to {to_addr}"
            
        except Exception as e:
            return f"Error sending email: {str(e)}"
    
    return f"Unknown email tool: {tool_name}"


async def email_check_loop(bot):
    """
    Background task that checks for new emails periodically.
    
    Should be started from on_ready() in the Discord bot.
    """
    await bot.wait_until_ready()
    
    monitor = get_email_monitor()
    print(f"[email] Starting email monitor for {EMAIL_ADDRESS}")
    print(f"[email] Will notify user ID {NOTIFY_USER_ID}")
    
    while not bot.is_closed():
        try:
            new_emails, error = monitor.get_new_emails()
            
            if error:
                print(f"[email] Error: {error}")
            elif new_emails:
                print(f"[email] {len(new_emails)} new email(s) detected!")
                
                # Get the user to notify
                try:
                    user = await bot.fetch_user(NOTIFY_USER_ID)
                    
                    for email_info in new_emails:
                        notification = (
                            f"📬 **New Email!**\n"
                            f"**From:** {email_info.from_addr}\n"
                            f"**Subject:** {email_info.subject}\n"
                            f"**Date:** {email_info.date}"
                        )
                        await user.send(notification)
                        print(f"[email] Notified about email from {email_info.from_addr}")
                        
                except Exception as e:
                    print(f"[email] Failed to notify user: {e}")
            
        except Exception as e:
            print(f"[email] Loop error: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)
